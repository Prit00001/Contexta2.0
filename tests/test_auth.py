import time

import jwt as pyjwt
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from core.auth import ROLE_RANK, generate_api_key, hash_api_key


def test_role_rank_orders_admin_above_member_above_viewer():
    assert ROLE_RANK["admin"] > ROLE_RANK["member"] > ROLE_RANK["viewer"]


def test_generate_api_key_has_recognizable_prefix_and_is_unique():
    a, b = generate_api_key(), generate_api_key()
    assert a.startswith("cxk_") and b.startswith("cxk_")
    assert a != b


def test_hash_api_key_is_deterministic_and_not_reversible_looking():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)
    assert hash_api_key(key) != key


# ---- T-02 / SR-31: JWT verification fails closed (defect D-02) ----


def _token(secret: str, **claims) -> str:
    payload = {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 60, **claims}
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _create_workspace(token: str):
    with TestClient(create_app()) as c:
        return c.post(
            "/workspaces", json={"org_name": "o", "workspace_name": "w"}, headers={"Authorization": f"Bearer {token}"}
        )


def test_jwt_rejected_with_503_when_secret_not_configured(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        r = _create_workspace(_token("any-secret-an-attacker-picks-1234567890"))
        assert r.status_code == 503
        assert "secret" not in r.text.lower() and "hmac" not in r.text.lower()
    finally:
        get_settings.cache_clear()


def test_forged_and_expired_tokens_get_generic_401(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "real-secret-for-tests-0123456789abcdef")
    get_settings.cache_clear()
    try:
        forged = _create_workspace(_token("attacker-secret-0123456789abcdefghij"))
        assert forged.status_code == 401 and forged.json()["detail"] == "Invalid token."
        expired = _create_workspace(_token("real-secret-for-tests-0123456789abcdef", exp=int(time.time()) - 10))
        assert expired.status_code == 401 and expired.json()["detail"] == "Token has expired."
    finally:
        get_settings.cache_clear()
