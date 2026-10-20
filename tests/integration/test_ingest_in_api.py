"""INGEST_IN_API=true: the API process ingests uploads itself, with no separate worker (single-service
hosts such as Render's free tier)."""

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


def test_upload_is_ingested_without_a_separate_worker(tmp_path, monkeypatch):
    for k, v in dict(
        STORAGE_BACKEND="local", STORAGE_DIR=str(tmp_path), SUPABASE_JWT_SECRET=JWT_SECRET, INGEST_IN_API="true"
    ).items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    uid = str(uuid.uuid4())
    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"solo-{uid[:6]}@test.com"))
    token = pyjwt.encode({"sub": uid, "aud": "authenticated", "exp": int(time.time()) + 600}, JWT_SECRET, "HS256")
    auth = {"Authorization": f"Bearer {token}"}
    org = None
    try:
        with TestClient(create_app()) as c:
            r = c.post("/workspaces", json={"org_name": "Solo", "workspace_name": "Docs"}, headers=auth)
            ws, org = r.json()["workspace_id"], r.json()["org_id"]
            text = b"# Leave policy\n\nEmployees get 20 vacation days a year. Unused days carry over up to 5.\n"
            r = c.post(f"/workspaces/{ws}/documents", files={"file": ("leave.md", text, "text/markdown")}, headers=auth)
            assert r.status_code == 200, r.text
            deadline = time.monotonic() + 60
            status = None
            while time.monotonic() < deadline:
                status = c.get(f"/workspaces/{ws}/documents", headers=auth).json()[0]["status"]
                if status not in ("queued", "processing"):
                    break
                time.sleep(0.5)
            assert status == "ready"
    finally:
        get_settings.cache_clear()
        with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            if org:
                cur.execute("DELETE FROM organizations WHERE id = %s", (org,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
