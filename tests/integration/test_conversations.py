"""Stored workspace conversations (P2-14, ADR-006) over HTTP against a real Postgres:
author-only visibility (admins and other members included), server-side follow-up history,
full source passages kept per turn, 30-day retention on read and by purge, and the RLS policy.
Skipped unless DATABASE_URL is set. No LLM provider is configured, so answers are extractive and
follow-ups use the no-LLM merge - the history plumbing is what is under test.
"""

from __future__ import annotations

import os
import time
import uuid

import jwt as pyjwt
import psycopg2
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="set DATABASE_URL to run DB integration tests")

JWT_SECRET = "test-jwt-secret-not-for-production"
POLICY = (
    "# Vacation Policy\n"
    "Employees get 20 paid vacation days per year, accrued monthly from the first day of employment. "
    "Unused days carry over to the next calendar year up to a maximum of five days; anything above that "
    "lapses on 31 March. Requests longer than ten consecutive working days need approval from a director "
    "at least six weeks ahead. Part-time employees accrue vacation pro rata to their contracted hours."
)  # longer than the 300-character response snippet, so storing the full passage is observable


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _auth(user_id: str) -> dict:
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm="HS256"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A workspace owned by ``owner`` with an ingested policy document, plus a second admin and a
    plain member. Yields (client, ws_id, users, doc_id)."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()
    users = {name: str(uuid.uuid4()) for name in ("owner", "admin", "member")}
    with _conn() as conn, conn.cursor() as cur:
        for name, uid in users.items():
            cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"{name}-{uid[:8]}@test.com"))
    org_id = None
    try:
        with TestClient(create_app()) as c:
            r = c.post("/workspaces", json={"org_name": "Acme", "workspace_name": "HQ"}, headers=_auth(users["owner"]))
            assert r.status_code == 200, r.text
            ws, org_id = r.json()["workspace_id"], r.json()["org_id"]
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s, %s, 'admin'), (%s, %s, 'member')",
                    (users["admin"], ws, users["member"], ws),
                )
            r = c.post(
                f"/workspaces/{ws}/documents",
                files={"file": ("policy.md", POLICY.encode(), "text/markdown")},
                headers=_auth(users["owner"]),
            )
            assert r.status_code == 200, r.text
            doc_id = r.json()["document_id"]

            from core import db as core_db
            from services.ingestion import run_ingestion

            ingest_conn = core_db.get_conn(DATABASE_URL)
            run_ingestion(ingest_conn, get_settings(), doc_id)
            ingest_conn.close()
            yield c, ws, users, doc_id
    finally:
        with _conn() as conn, conn.cursor() as cur:
            if org_id:
                cur.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
            cur.execute("DELETE FROM auth.users WHERE id = ANY(%s::uuid[])", (list(users.values()),))
        get_settings.cache_clear()


def _ask(c, ws, user, **body):
    return c.post(f"/workspaces/{ws}/ask", json={"question": "How many vacation days do we get?", **body},
                  headers=_auth(user))  # fmt: skip


def test_save_follow_up_read_and_delete(workspace):
    c, ws, users, _ = workspace
    owner = users["owner"]

    r = _ask(c, ws, owner, save=True)
    assert r.status_code == 200, r.text
    cid = r.json()["conversation_id"]
    assert cid and r.json()["standalone_question"] is None  # first turn: nothing to resolve

    # Follow-up: the server supplies the stored turn as history (no-LLM merge prepends it).
    r = _ask(c, ws, owner, question="What about carrying them over?", conversation_id=cid)
    assert r.status_code == 200, r.text
    assert r.json()["conversation_id"] == cid
    assert "How many vacation days do we get?" in r.json()["standalone_question"]
    assert "contextualize" in [w["stage"] for w in r.json()["workflow"]]

    (listed,) = c.get(f"/workspaces/{ws}/conversations", headers=_auth(owner)).json()
    assert listed["id"] == cid and listed["turns"] == 2 and listed["title"] == "How many vacation days do we get?"

    detail = c.get(f"/workspaces/{ws}/conversations/{cid}", headers=_auth(owner)).json()
    first, second = detail["turns"]
    assert (first["position"], second["position"]) == (1, 2)
    assert second["question"] == "What about carrying them over?" and second["standalone_question"]
    assert first["sources"] and first["sources"][0]["filename"] == "policy.md"
    assert "Part-time employees accrue" in first["sources"][0]["text"]  # the full passage, not the snippet
    assert detail["expires_at"] > detail["updated_at"]

    assert c.delete(f"/workspaces/{ws}/conversations/{cid}", headers=_auth(owner)).status_code == 204
    assert c.get(f"/workspaces/{ws}/conversations/{cid}", headers=_auth(owner)).status_code == 404
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversation_turns WHERE conversation_id = %s", (cid,))
        assert cur.fetchone()[0] == 0  # turns go with the conversation


def test_only_the_author_can_see_or_use_a_conversation(workspace):
    c, ws, users, _ = workspace
    cid = _ask(c, ws, users["owner"], save=True).json()["conversation_id"]
    for other in ("admin", "member"):  # admins get no extra access
        h = _auth(users[other])
        assert c.get(f"/workspaces/{ws}/conversations", headers=h).json() == []
        assert c.get(f"/workspaces/{ws}/conversations/{cid}", headers=h).status_code == 404
        assert _ask(c, ws, users[other], conversation_id=cid).status_code == 404
        assert c.delete(f"/workspaces/{ws}/conversations/{cid}", headers=h).status_code == 404
    assert c.get(f"/workspaces/{ws}/conversations/{cid}", headers=_auth(users["owner"])).status_code == 200


def test_api_keys_and_mixed_history_are_refused(workspace):
    c, ws, users, _ = workspace
    r = c.post(f"/workspaces/{ws}/api-keys", json={"name": "ci"}, headers=_auth(users["owner"]))
    key = {"Authorization": f"Bearer {r.json()['api_key']}"}
    q = {"question": "How many vacation days do we get?"}
    assert c.post(f"/workspaces/{ws}/ask", json=q | {"save": True}, headers=key).status_code == 403
    assert c.get(f"/workspaces/{ws}/conversations", headers=key).status_code == 403
    # stateless history still works for API keys
    hist = [{"question": "Tell me about vacation.", "answer": "There is a policy [1]."}]
    r = c.post(f"/workspaces/{ws}/ask", json=q | {"history": hist}, headers=key)
    assert r.status_code == 200 and r.json()["standalone_question"] and r.json()["conversation_id"] is None

    cid = _ask(c, ws, users["owner"], save=True).json()["conversation_id"]
    assert _ask(c, ws, users["owner"], conversation_id=cid, history=hist).status_code == 422
    assert _ask(c, ws, users["owner"], conversation_id=cid, save=True).status_code == 422


def test_retention_hides_then_purges_idle_conversations(workspace):
    c, ws, users, _ = workspace
    owner = users["owner"]
    old = _ask(c, ws, owner, save=True).json()["conversation_id"]
    fresh = _ask(c, ws, owner, save=True).json()["conversation_id"]
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE conversations SET updated_at = now() - interval '31 days' WHERE id = %s", (old,))

    # Hidden on every read before any purge runs
    assert [x["id"] for x in c.get(f"/workspaces/{ws}/conversations", headers=_auth(owner)).json()] == [fresh]
    assert c.get(f"/workspaces/{ws}/conversations/{old}", headers=_auth(owner)).status_code == 404
    assert _ask(c, ws, owner, conversation_id=old).status_code == 404

    from core import db as core_db
    from services.worker import purge_history

    conn = core_db.get_conn(DATABASE_URL)
    try:
        assert purge_history(conn, get_settings()) >= 1
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM conversations WHERE id = ANY(%s::uuid[])", ([old, fresh],))
            assert [str(r[0]) for r in cur.fetchall()] == [fresh]
            cur.execute("SELECT count(*) FROM conversation_turns WHERE conversation_id = %s", (old,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_passages_outlive_a_deleted_document(workspace):
    """The owner's chosen trade-off (ADR-006): a thread keeps the passages it was answered from,
    even after the document is deleted, until the thread itself expires or is deleted."""
    c, ws, users, doc_id = workspace
    cid = _ask(c, ws, users["owner"], save=True).json()["conversation_id"]
    assert c.delete(f"/workspaces/{ws}/documents/{doc_id}", headers=_auth(users["owner"])).status_code == 204
    turn = c.get(f"/workspaces/{ws}/conversations/{cid}", headers=_auth(users["owner"])).json()["turns"][0]
    assert "Part-time employees accrue" in turn["sources"][0]["text"]


def _rls_visible(user_id: str, cid: str) -> tuple[int, int]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SET ROLE authenticated")
        cur.execute("SET LOCAL request.jwt.claims = %s", ('{"sub": "%s"}' % user_id,))
        cur.execute("SELECT count(*) FROM conversations WHERE id = %s", (cid,))
        conversations = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM conversation_turns WHERE conversation_id = %s", (cid,))
        return conversations, cur.fetchone()[0]


def test_rls_policy_is_author_only(workspace):
    c, ws, users, _ = workspace
    cid = _ask(c, ws, users["owner"], save=True).json()["conversation_id"]
    assert _rls_visible(users["owner"], cid) == (1, 1)
    assert _rls_visible(users["admin"], cid) == (0, 0)
    assert _rls_visible(users["member"], cid) == (0, 0)
    with _conn() as conn, conn.cursor() as cur:  # the author leaves the workspace
        cur.execute("DELETE FROM memberships WHERE user_id = %s AND workspace_id = %s", (users["owner"], ws))
    assert _rls_visible(users["owner"], cid)[0] == 0
