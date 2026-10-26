"""Billing end to end against Postgres (ADR-007), Razorpay mocked at the HTTP layer: the trial
clock, the 402 paywall, subscribe -> Checkout signature -> paid, the signed webhook, seat sync."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
import jwt as pyjwt
import psycopg2
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="set DATABASE_URL to run DB integration tests")

JWT_SECRET = "test-jwt-secret-not-for-production"
KEY_SECRET, WEBHOOK_SECRET = "rzp-key-secret", "rzp-webhook-secret"


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _auth(user_id: str) -> dict:
    t = pyjwt.encode({"sub": user_id, "aud": "authenticated", "exp": int(time.time()) + 3600}, JWT_SECRET, "HS256")
    return {"Authorization": f"Bearer {t}"}


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


@pytest.fixture()
def billed(tmp_path, monkeypatch):
    """A workspace with an admin and a viewer, billing on, Razorpay replaced by a recorder."""
    env = dict(
        STORAGE_BACKEND="local", STORAGE_DIR=str(tmp_path), SUPABASE_JWT_SECRET=JWT_SECRET, BILLING_ENABLED="true",
        BILLING_TRIAL_MINUTES="60", RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET=KEY_SECRET,
        RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET, RAZORPAY_PLAN_ID="plan_seat_monthly", BILLING_PRICE_LABEL="₹499 per seat / month",
    )  # fmt: skip
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    calls: list[tuple[str, str, dict]] = []

    def razorpay(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        calls.append((req.method, req.url.path, body))
        if req.url.host != "api.razorpay.com":
            return httpx.Response(404)
        if req.method == "POST" and req.url.path == "/v1/subscriptions":
            return httpx.Response(200, json={"id": "sub_test_1", "status": "created", "quantity": body["quantity"]})
        return httpx.Response(200, json={"id": "sub_test_1", "status": "active"})

    users = {"admin": str(uuid.uuid4()), "viewer": str(uuid.uuid4())}
    with _conn() as conn, conn.cursor() as cur:
        for name, uid in users.items():
            cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"{name}-{uid[:6]}@test.com"))
    org = None
    try:
        with TestClient(create_app(http_transport=httpx.MockTransport(razorpay))) as c:
            r = c.post(
                "/workspaces", json={"org_name": "Acme", "workspace_name": "Paid"}, headers=_auth(users["admin"])
            )
            ws, org = r.json()["workspace_id"], r.json()["org_id"]
            with _conn() as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s, %s, 'viewer')",
                            (users["viewer"], ws))  # fmt: skip
            yield c, ws, users, calls
    finally:
        with _conn() as conn, conn.cursor() as cur:
            if org:
                cur.execute("DELETE FROM organizations WHERE id = %s", (org,))
            cur.execute("DELETE FROM auth.users WHERE id = ANY(%s::uuid[])", (list(users.values()),))
        get_settings.cache_clear()


def _set_clock(ws: str, seconds: int, last_active_ago_s: int | None = None):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workspace_billing (workspace_id, active_seconds, last_active_at) VALUES (%s, %s, "
            "now() - make_interval(secs => %s)) ON CONFLICT (workspace_id) DO UPDATE SET "
            "active_seconds = EXCLUDED.active_seconds, last_active_at = EXCLUDED.last_active_at",
            (ws, seconds, last_active_ago_s if last_active_ago_s is not None else 100000),
        )


def _ask(c, ws, user):
    return c.post(f"/workspaces/{ws}/ask", json={"question": "What is our vacation policy?"}, headers=_auth(user))


def test_trial_clock_counts_only_short_gaps(billed):
    c, ws, users, _ = billed
    b = c.post(f"/workspaces/{ws}/activity", headers=_auth(users["viewer"])).json()
    assert b["plan"] == "trial" and b["trial_seconds_left"] == 3600 and b["seats"] == 2
    _set_clock(ws, 100, last_active_ago_s=60)  # active a minute ago: that minute counts
    b = c.post(f"/workspaces/{ws}/activity", headers=_auth(users["viewer"])).json()
    assert 3600 - 160 - 2 <= b["trial_seconds_left"] <= 3600 - 160
    _set_clock(ws, 100, last_active_ago_s=3 * 3600)  # back after three idle hours: nothing added
    b = c.post(f"/workspaces/{ws}/activity", headers=_auth(users["viewer"])).json()
    assert b["trial_seconds_left"] == 3500


def test_expired_trial_blocks_asking_and_uploading_but_not_reading(billed):
    c, ws, users, _ = billed
    _set_clock(ws, 3600)
    r = _ask(c, ws, users["admin"])
    assert r.status_code == 402 and "free trial has ended" in r.json()["detail"]
    r = c.post(f"/workspaces/{ws}/documents", files={"file": ("a.md", b"# A\ntext", "text/markdown")},
               headers=_auth(users["admin"]))  # fmt: skip
    assert r.status_code == 402
    assert c.get(f"/workspaces/{ws}/documents", headers=_auth(users["viewer"])).status_code == 200
    assert c.get(f"/workspaces/{ws}/conversations", headers=_auth(users["viewer"])).status_code == 200
    b = c.get(f"/workspaces/{ws}/billing", headers=_auth(users["viewer"])).json()
    assert b["plan"] == "expired" and b["can_manage"] is False and b["price_label"] == "₹499 per seat / month"


def test_subscribe_verify_then_webhook_keeps_it_current(billed):
    c, ws, users, calls = billed
    _set_clock(ws, 3600)
    assert c.post(f"/workspaces/{ws}/billing/subscribe", headers=_auth(users["viewer"])).status_code == 403

    r = c.post(f"/workspaces/{ws}/billing/subscribe", headers=_auth(users["admin"]))
    assert r.status_code == 200 and r.json() == {"subscription_id": "sub_test_1", "key_id": "rzp_test_key", "seats": 2}
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/v1/subscriptions")
    assert body["plan_id"] == "plan_seat_monthly" and body["quantity"] == 2 and body["notes"] == {"workspace_id": ws}

    bad = {"razorpay_payment_id": "pay_1", "razorpay_subscription_id": "sub_test_1", "razorpay_signature": "nope"}
    assert c.post(f"/workspaces/{ws}/billing/verify", json=bad, headers=_auth(users["admin"])).status_code == 400
    good = bad | {"razorpay_signature": _sign(KEY_SECRET, b"pay_1|sub_test_1")}
    b = c.post(f"/workspaces/{ws}/billing/verify", json=good, headers=_auth(users["admin"])).json()
    assert b["plan"] == "active" and b["seats_billed"] == 2
    assert _ask(c, ws, users["viewer"]).status_code == 200  # paid: asking works again

    def hook(status: str, signature: str | None = None):
        raw = json.dumps({"event": f"subscription.{status}", "payload": {"subscription": {"entity": {
            "id": "sub_test_1", "status": status, "quantity": 2, "current_end": 1893456000}}}}).encode()  # fmt: skip
        sig = signature if signature is not None else _sign(WEBHOOK_SECRET, raw)
        return c.post("/billing/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": sig,
                      "Content-Type": "application/json"})  # fmt: skip

    assert hook("halted", signature="forged").status_code == 400
    assert hook("halted").status_code == 200
    assert _ask(c, ws, users["viewer"]).status_code == 402  # payments failed for good: paywall again
    assert hook("active").status_code == 200
    b = c.get(f"/workspaces/{ws}/billing", headers=_auth(users["admin"])).json()
    assert b["plan"] == "active" and b["current_end"].startswith("2030-01-01") and b["can_manage"] is True


def test_removing_a_member_updates_seats_at_renewal(billed):
    c, ws, users, calls = billed
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO workspace_billing (workspace_id, razorpay_subscription_id, subscription_status, "
                    "seats_billed) VALUES (%s, 'sub_test_1', 'active', 2)", (ws,))  # fmt: skip
    assert c.delete(f"/workspaces/{ws}/members/{users['viewer']}", headers=_auth(users["admin"])).status_code == 204
    method, path, body = calls[-1]
    assert (method, path) == ("PATCH", "/v1/subscriptions/sub_test_1")
    assert body == {"quantity": 1, "schedule_change_at": "cycle_end"}
    assert c.get(f"/workspaces/{ws}/billing", headers=_auth(users["admin"])).json()["seats_billed"] == 1


def test_billing_off_means_unlimited(billed, monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "false")
    get_settings.cache_clear()
    c0, ws, users, _ = billed
    with TestClient(create_app()) as c:
        _set_clock(ws, 999999)
        assert c.get(f"/workspaces/{ws}/billing", headers=_auth(users["admin"])).json()["plan"] == "unlimited"
        assert _ask(c, ws, users["admin"]).status_code == 200
