"""Sync Postgres access for ingestion and the worker. Kept synchronous deliberately:
the worker is a plain polling loop with no need for async, and any FastAPI route that
calls into this (Phase 1c) wraps it in asyncio.to_thread, the same pattern
core/embeddings.py already uses for its sync embed_sync()."""

from __future__ import annotations

from typing import Optional

import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2 import pool as _pool


def get_conn(dsn: str):
    conn = psycopg2.connect(dsn)
    register_vector(conn)
    return conn


def make_pool(dsn: str, minconn: int = 1, maxconn: int = 10):
    """A small connection pool for the FastAPI app (one process, many requests).
    The worker uses a single long-lived connection instead (see services/worker.py)."""
    return _pool.ThreadedConnectionPool(minconn, maxconn, dsn)


def get_pool_conn(pool):
    conn = pool.getconn()
    register_vector(conn)  # idempotent; the pool may hand back a connection we haven't seen yet
    return conn


def put_pool_conn(pool, conn) -> None:
    pool.putconn(conn)


def get_document(conn, document_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, workspace_id, access_group_id, filename, content_type, storage_path, status "
            "FROM documents WHERE id = %s",
            (document_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"document {document_id} not found")
    keys = ["id", "workspace_id", "access_group_id", "filename", "content_type", "storage_path", "status"]
    return dict(zip(keys, row, strict=True))


def set_document_status(conn, document_id: str, status: str, error: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE documents SET status=%s, error=%s, updated_at=now() WHERE id=%s",
            (status, error, document_id),
        )
    conn.commit()


def upsert_document(
    conn,
    *,
    workspace_id: str,
    filename: str,
    content_type: str,
    storage_path: str,
    content_hash: str,
    access_group_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
):
    """Returns (document_id, needs_ingestion). needs_ingestion is False when an
    identical file (same content_hash) is already stored under this filename."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, content_hash FROM documents WHERE workspace_id=%s AND filename=%s",
            (workspace_id, filename),
        )
        row = cur.fetchone()
        if row and row[1] == content_hash:
            return row[0], False
        if row:
            doc_id = row[0]
            cur.execute(
                "UPDATE documents SET content_type=%s, storage_path=%s, content_hash=%s, status='queued', "
                "error=NULL, access_group_id=%s, updated_at=now() WHERE id=%s",
                (content_type, storage_path, content_hash, access_group_id, doc_id),
            )
            cur.execute("DELETE FROM chunks WHERE document_id=%s", (doc_id,))
        else:
            cur.execute(
                "INSERT INTO documents (workspace_id, filename, content_type, storage_path, content_hash, "
                "access_group_id, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (workspace_id, filename, content_type, storage_path, content_hash, access_group_id, uploaded_by),
            )
            doc_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO jobs (workspace_id, document_id, job_type) VALUES (%s, %s, 'ingest')",
            (workspace_id, doc_id),
        )
    conn.commit()
    return doc_id, True


def replace_chunks(conn, document_id: str, workspace_id: str, access_group_id, chunks, vectors) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE document_id=%s", (document_id,))
        for chunk, vec in zip(chunks, vectors, strict=True):
            cur.execute(
                "INSERT INTO chunks (document_id, workspace_id, access_group_id, chunk_index, page_number, "
                "section_heading, content, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    document_id,
                    workspace_id,
                    access_group_id,
                    chunk.chunk_index,
                    chunk.page_number,
                    chunk.section_heading,
                    chunk.content,
                    vec,
                ),
            )
    conn.commit()


def claim_job(conn) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='processing', updated_at=now() WHERE id = ("
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ") RETURNING id, workspace_id, document_id, job_type"
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return None
    keys = ["id", "workspace_id", "document_id", "job_type"]
    return dict(zip(keys, row, strict=True))


def mark_job(conn, job_id: str, status: str, error: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status=%s, error=%s, attempts=attempts+1, updated_at=now() WHERE id=%s",
            (status, error, job_id),
        )
    conn.commit()


# ---- Workspace bootstrap, membership, API keys (Phase 1d) ----


def create_org_workspace(conn, *, org_name: str, workspace_name: str, admin_user_id: str):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id", (org_name,))
        org_id = cur.fetchone()[0]
        cur.execute("INSERT INTO workspaces (org_id, name) VALUES (%s, %s) RETURNING id", (org_id, workspace_name))
        ws_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s, %s, 'admin')",
            (admin_user_id, ws_id),
        )
    conn.commit()
    return str(org_id), str(ws_id)


def add_membership(conn, *, workspace_id: str, user_id: str, role: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s,%s,%s) "
            "ON CONFLICT (user_id, workspace_id) DO UPDATE SET role=EXCLUDED.role",
            (user_id, workspace_id, role),
        )
    conn.commit()


def remove_membership(conn, *, workspace_id: str, user_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memberships WHERE workspace_id=%s AND user_id=%s", (workspace_id, user_id))
    conn.commit()


def list_memberships(conn, workspace_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT m.user_id, m.role, m.created_at, u.email FROM memberships m "
            "LEFT JOIN auth.users u ON u.id = m.user_id WHERE m.workspace_id = %s ORDER BY m.created_at",
            (workspace_id,),
        )
        return cur.fetchall()


def user_workspaces(conn, user_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT w.id, w.name, m.role, w.org_id FROM memberships m JOIN workspaces w ON w.id = m.workspace_id "
            "WHERE m.user_id = %s ORDER BY w.name",
            (user_id,),
        )
        return cur.fetchall()


def visible_group_ids(conn, user_id: str):
    with conn.cursor() as cur:
        cur.execute("SELECT group_id FROM access_group_members WHERE user_id=%s", (user_id,))
        return [r[0] for r in cur.fetchall()]


def create_api_key(conn, *, workspace_id: str, name: str, hashed_key: str, created_by: Optional[str]):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys (workspace_id, name, hashed_key, created_by) VALUES (%s,%s,%s,%s) "
            "RETURNING id, created_at",
            (workspace_id, name, hashed_key, created_by),
        )
        row = cur.fetchone()
    conn.commit()
    return row


def list_api_keys(conn, workspace_id: str):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, created_at, last_used_at, revoked_at FROM api_keys "
            "WHERE workspace_id=%s ORDER BY created_at DESC",
            (workspace_id,),
        )
        return cur.fetchall()


def revoke_api_key(conn, workspace_id: str, key_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET revoked_at=now() WHERE id=%s AND workspace_id=%s AND revoked_at IS NULL RETURNING id",
            (key_id, workspace_id),
        )
        row = cur.fetchone()
    conn.commit()
    return row is not None
