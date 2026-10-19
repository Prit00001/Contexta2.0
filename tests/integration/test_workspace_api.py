"""End-to-end over HTTP, now behind real auth: mint a Supabase-shaped JWT locally
(HMAC against a test secret - no network call), bootstrap a workspace through the
actual API, upload+ingest+ask, prove cross-workspace isolation and role enforcement,
and exercise the API-key path (create, use without a JWT, revoke). Skipped unless
DATABASE_URL is set.
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


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _token(user_id: str) -> str:
    return pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm="HS256"
    )


def test_upload_ask_isolation_and_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    get_settings.cache_clear()

    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    token_a, token_b = _token(user_a), _token(user_b)
    org_a = org_b = None

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, 'a@test.com'), (%s, 'b@test.com')", (user_a, user_b)
        )

    try:
        with TestClient(create_app()) as c:
            # Bootstrap a workspace for user A, and an unrelated one for user B, via the real API.
            r = c.post(
                "/workspaces",
                json={"org_name": "Acme", "workspace_name": "Acme HQ"},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r.status_code == 200, r.text
            ws_a, org_a = r.json()["workspace_id"], r.json()["org_id"]

            r = c.post(
                "/workspaces",
                json={"org_name": "Other Co", "workspace_name": "Other HQ"},
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert r.status_code == 200, r.text
            org_b = r.json()["org_id"]

            # User B has no membership in A's workspace at all.
            r = c.get(f"/workspaces/{ws_a}/documents", headers={"Authorization": f"Bearer {token_b}"})
            assert r.status_code == 403

            # Upload + ingest + ask within A's own workspace, as A.
            content = b"# Vacation Policy\nEmployees get 20 paid vacation days per year, accrued monthly."
            r = c.post(
                f"/workspaces/{ws_a}/documents",
                files={"file": ("policy.md", content, "text/markdown")},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r.status_code == 200, r.text
            doc_id = r.json()["document_id"]

            from core import db as core_db
            from services.ingestion import run_ingestion

            ingest_conn = core_db.get_conn(DATABASE_URL)
            run_ingestion(ingest_conn, get_settings(), doc_id)
            ingest_conn.close()

            r = c.post(
                f"/workspaces/{ws_a}/ask",
                json={"question": "How many vacation days do employees get?"},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["sources"][0]["filename"] == "policy.md"
            # P2-FR-06: workspace Q&A runs on the same graph as /ask - same citation fields exist.
            assert body["outcome"] in ("answered", "extractive_fallback", "insufficient_evidence")
            assert body["citation_check"]["verification"] == "marker_only"
            assert [w["stage"] for w in body["workflow"]][:2] == ["validate_request", "retrieve"]

            # Grant B viewer access to A's workspace (bypassing the email-lookup admin endpoint,
            # which needs a real Supabase project) - a viewer can read but not upload.
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s, %s, 'viewer')", (user_b, ws_a)
                )
            r = c.post(
                f"/workspaces/{ws_a}/documents",
                files={"file": ("x.txt", b"hi", "text/plain")},
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert r.status_code == 403

            # API key: create (admin only), use it without a JWT, then revoke it.
            r = c.post(
                f"/workspaces/{ws_a}/api-keys", json={"name": "ci"}, headers={"Authorization": f"Bearer {token_a}"}
            )
            assert r.status_code == 200, r.text
            key_id, api_key = r.json()["id"], r.json()["api_key"]

            r = c.post(
                f"/workspaces/{ws_a}/ask",
                json={"question": "vacation days?"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert r.status_code == 200, r.text

            r = c.delete(f"/workspaces/{ws_a}/api-keys/{key_id}", headers={"Authorization": f"Bearer {token_a}"})
            assert r.status_code == 204

            r = c.post(
                f"/workspaces/{ws_a}/ask",
                json={"question": "vacation days?"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            assert r.status_code == 401

            # Magic-byte check: a .pdf extension whose bytes aren't a real PDF is rejected.
            r = c.post(
                f"/workspaces/{ws_a}/documents",
                files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r.status_code == 400

            # Path traversal (D-01): a filename aimed at another workspace's prefix is stored under
            # its sanitised basename inside this workspace, and nothing lands outside STORAGE_DIR.
            r = c.post(
                f"/workspaces/{ws_a}/documents",
                files={"file": (f"../{org_b}/../../escape.md", b"# Note\nTraversal attempt.", "text/markdown")},
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["filename"] == "escape.md"
            assert (tmp_path / ws_a / "escape.md").exists()
            assert not (tmp_path.parent / "escape.md").exists()
            c.delete(
                f"/workspaces/{ws_a}/documents/{r.json()['document_id']}",
                headers={"Authorization": f"Bearer {token_a}"},
            )

            c.delete(f"/workspaces/{ws_a}/documents/{doc_id}", headers={"Authorization": f"Bearer {token_a}"})
    finally:
        with _conn() as conn, conn.cursor() as cur:
            for org_id in (org_a, org_b):
                if org_id:
                    cur.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
            cur.execute("DELETE FROM auth.users WHERE id IN (%s, %s)", (user_a, user_b))
        get_settings.cache_clear()
