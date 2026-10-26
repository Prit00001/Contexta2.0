"""Workspace document + Q&A endpoints: upload, list, delete, and the real grounded-
answer endpoint over a workspace's own ingested chunks (as opposed to the arXiv demo
at /ask). Every route requires a Supabase JWT or workspace API key (app/deps.py),
rate-limited via Redis/Upstash (core/rate_limit.py, in-memory fallback if unconfigured).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.deps import AuthContext, require_role
from app.routes_billing import charge_activity
from app.schemas import (
    ConversationDetail,
    ConversationOut,
    DocumentOut,
    UploadResponse,
    WorkspaceAskRequest,
    WorkspaceAskResponse,
)
from core import conversations, db
from core.followup import MAX_TURNS
from core.storage import get_storage, safe_filename
from services import worker
from services.workspace_qa import ask_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}")

_EXT_CONTENT_TYPE = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
_MAGIC_BYTES = {
    "application/pdf": b"%PDF-",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
}


def _content_type_for(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ct = _EXT_CONTENT_TYPE.get(ext)
    if not ct:
        raise HTTPException(400, f"Unsupported file type: {filename}. Allowed: PDF, DOCX, TXT, MD.")
    return ct


def _check_magic_bytes(data: bytes, content_type: str, filename: str) -> None:
    magic = _MAGIC_BYTES.get(content_type)
    if magic and not data.startswith(magic):
        raise HTTPException(400, f"{filename} does not look like a valid file for its extension.")


async def _check_rate_limit(request: Request, workspace_id: str, ctx: AuthContext) -> None:
    limiter = request.app.state.workspace_limiter
    key = f"{workspace_id}:{ctx.user_id or ctx.via}"
    if not await limiter.allow(key):
        raise HTTPException(
            429, "Too many requests. Please wait a minute and try again.", headers={"Retry-After": "60"}
        )


async def _allowed_group_ids(pool, ctx: AuthContext):
    """None = no restriction (admin or API key). Otherwise the list of access groups the
    authenticated user belongs to (possibly empty = only ungrouped documents)."""
    if ctx.via == "api_key" or ctx.role == "admin":
        return None

    def _lookup():
        conn = db.get_pool_conn(pool)
        try:
            return db.visible_group_ids(conn, ctx.user_id)
        finally:
            db.put_pool_conn(pool, conn)

    return await asyncio.to_thread(_lookup)


@router.post("/documents", response_model=UploadResponse)
async def upload_document(
    workspace_id: str,
    request: Request,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(require_role("member")),
):
    await _check_rate_limit(request, workspace_id, ctx)
    await charge_activity(request, workspace_id)
    settings = request.app.state.settings
    pool = request.app.state.db_pool
    max_bytes = int(settings.max_upload_mb * 1024 * 1024)
    data = await file.read(max_bytes + 1)  # never buffer more than the limit
    if len(data) > max_bytes:
        raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB upload limit.")
    # Client-supplied names may contain "../" or another workspace's prefix (D-01); only the
    # sanitised basename is ever used for storage paths, the DB row and responses.
    filename = safe_filename(file.filename)
    content_type = _content_type_for(filename)
    _check_magic_bytes(data, content_type, filename)
    storage_path = f"{workspace_id}/{filename}"
    content_hash = hashlib.sha256(data).hexdigest()

    def _write():
        get_storage(settings).save(storage_path, data)
        conn = db.get_pool_conn(pool)
        try:
            return db.upsert_document(
                conn,
                workspace_id=workspace_id,
                filename=filename,
                content_type=content_type,
                storage_path=storage_path,
                content_hash=content_hash,
                uploaded_by=ctx.user_id,
            )
        finally:
            db.put_pool_conn(pool, conn)

    doc_id, needs_ingest = await asyncio.to_thread(_write)
    status = "queued" if needs_ingest else "ready"
    if needs_ingest and settings.ingest_inline:  # serverless: nothing would ingest it after we respond

        def _ingest_now():
            conn = db.get_pool_conn(pool)
            try:
                worker.drain(conn, settings)
                return db.get_document(conn, str(doc_id))["status"]
            finally:
                db.put_pool_conn(pool, conn)

        status = await asyncio.to_thread(_ingest_now)
    return UploadResponse(document_id=str(doc_id), filename=filename, status=status)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(workspace_id: str, request: Request, ctx: AuthContext = Depends(require_role("viewer"))):
    await _check_rate_limit(request, workspace_id, ctx)
    pool = request.app.state.db_pool
    group_ids = await _allowed_group_ids(pool, ctx)

    def _list():
        conn = db.get_pool_conn(pool)
        try:
            with conn.cursor() as cur:
                if group_ids is None:
                    cur.execute(
                        "SELECT id, filename, status, error, created_at FROM documents "
                        "WHERE workspace_id=%s ORDER BY created_at DESC",
                        (workspace_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, filename, status, error, created_at FROM documents "
                        "WHERE workspace_id=%s AND (access_group_id IS NULL OR access_group_id = ANY(%s)) "
                        "ORDER BY created_at DESC",
                        (workspace_id, group_ids),
                    )
                return cur.fetchall()
        finally:
            db.put_pool_conn(pool, conn)

    rows = await asyncio.to_thread(_list)
    return [
        DocumentOut(id=str(r[0]), filename=r[1], status=r[2], error=r[3], created_at=r[4].isoformat()) for r in rows
    ]


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    workspace_id: str, document_id: str, request: Request, ctx: AuthContext = Depends(require_role("member"))
):
    await _check_rate_limit(request, workspace_id, ctx)
    settings = request.app.state.settings
    pool = request.app.state.db_pool

    def _delete():
        conn = db.get_pool_conn(pool)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT storage_path FROM documents WHERE id=%s AND workspace_id=%s",
                    (document_id, workspace_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise HTTPException(404, "Document not found in this workspace.")
                storage_path = row[0]
                cur.execute("DELETE FROM documents WHERE id=%s", (document_id,))
            conn.commit()
            get_storage(settings).delete(storage_path)
        finally:
            db.put_pool_conn(pool, conn)

    await asyncio.to_thread(_delete)


def _require_user(ctx: AuthContext) -> str:
    """Stored conversations belong to a person; a workspace API key has no user to own them."""
    if not ctx.user_id:
        raise HTTPException(403, "Stored conversations need a signed-in user; API keys can send `history` instead.")
    return ctx.user_id


async def _in_thread(pool, fn, *args, **kwargs):
    """Run ``fn(conn, *args, **kwargs)`` on a pooled connection in a worker thread."""

    def _run():
        conn = db.get_pool_conn(pool)
        try:
            return fn(conn, *args, **kwargs)
        finally:
            db.put_pool_conn(pool, conn)

    return await asyncio.to_thread(_run)


@router.post("/ask", response_model=WorkspaceAskResponse)
async def ask(
    workspace_id: str, body: WorkspaceAskRequest, request: Request, ctx: AuthContext = Depends(require_role("viewer"))
):
    await _check_rate_limit(request, workspace_id, ctx)
    await charge_activity(request, workspace_id)
    pool = request.app.state.db_pool
    st = request.app.state
    retention = st.settings.history_retention_days
    cid = str(body.conversation_id) if body.conversation_id else None
    history = [t.model_dump() for t in body.history]
    user_id = _require_user(ctx) if cid or body.save else None
    if cid:
        if not await _in_thread(
            pool, conversations.get_conversation, cid, workspace_id=workspace_id, user_id=user_id,
            retention_days=retention,
        ):  # fmt: skip
            raise HTTPException(404, "Conversation not found.")
        turns = await _in_thread(pool, conversations.get_turns, cid, last=MAX_TURNS)
        history = [{"question": t["question"], "answer": t["answer"]} for t in turns]
    group_ids = await _allowed_group_ids(pool, ctx)
    conn = await asyncio.to_thread(db.get_pool_conn, pool)
    try:
        result = await ask_workspace(
            conn, st.service.graph, st.embedder, workspace_id, body.question, body.top_k, group_ids, history or None
        )
    finally:
        await asyncio.to_thread(db.put_pool_conn, pool, conn)
    stored_sources = result.pop("stored_sources")
    if body.save:  # created only once there is an answer to store, so a failed ask leaves no empty thread
        cid = await _in_thread(
            pool, conversations.create_conversation, workspace_id=workspace_id, user_id=user_id,
            title=conversations.title_from(result["question"]),
        )  # fmt: skip
    if cid:
        turn = {k: result[k] for k in ("question", "standalone_question", "answer", "outcome", "citations", "claims")}
        await _in_thread(pool, conversations.append_turn, cid, turn | {"sources": stored_sources})
    return WorkspaceAskResponse(**result, conversation_id=cid)


# ---- Stored conversations (P2-14, ADR-006): visible only to their author ----


def _iso(t: dt.datetime) -> str:
    return t.isoformat()


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    workspace_id: str, request: Request, ctx: AuthContext = Depends(require_role("viewer"))
):
    await _check_rate_limit(request, workspace_id, ctx)
    rows = await _in_thread(
        request.app.state.db_pool, conversations.list_conversations, workspace_id=workspace_id,
        user_id=_require_user(ctx), retention_days=request.app.state.settings.history_retention_days,
    )  # fmt: skip
    return [
        ConversationOut(
            id=r["id"], title=r["title"], created_at=_iso(r["created_at"]), updated_at=_iso(r["updated_at"]),
            turns=r["turns"],
        )  # fmt: skip
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    workspace_id: str, conversation_id: UUID, request: Request, ctx: AuthContext = Depends(require_role("viewer"))
):
    await _check_rate_limit(request, workspace_id, ctx)
    pool, retention = request.app.state.db_pool, request.app.state.settings.history_retention_days
    conv = await _in_thread(
        pool, conversations.get_conversation, str(conversation_id), workspace_id=workspace_id,
        user_id=_require_user(ctx), retention_days=retention,
    )  # fmt: skip
    if conv is None:
        raise HTTPException(404, "Conversation not found.")
    turns = await _in_thread(pool, conversations.get_turns, conv["id"])
    return ConversationDetail(
        id=conv["id"],
        title=conv["title"],
        created_at=_iso(conv["created_at"]),
        updated_at=_iso(conv["updated_at"]),
        expires_at=_iso(conv["updated_at"] + dt.timedelta(days=retention)),
        turns=[t | {"created_at": _iso(t["created_at"])} for t in turns],
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    workspace_id: str, conversation_id: UUID, request: Request, ctx: AuthContext = Depends(require_role("viewer"))
):
    await _check_rate_limit(request, workspace_id, ctx)
    deleted = await _in_thread(
        request.app.state.db_pool, conversations.delete_conversation, str(conversation_id),
        workspace_id=workspace_id, user_id=_require_user(ctx),
    )  # fmt: skip
    if not deleted:
        raise HTTPException(404, "Conversation not found.")
