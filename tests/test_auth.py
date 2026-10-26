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


# ---- Supabase JWT signing keys: ES256 tokens verified against the project's JWKS ----


def _es256():
    import json

    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(pyjwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    return key, {"keys": [{**jwk, "kid": "k1", "alg": "ES256", "use": "sig"}]}


def _decode(token, monkeypatch, jwks, url="https://proj.supabase.co", secret=""):
    from types import SimpleNamespace

    from app import deps

    fetched = []

    def fetch_data(self):
        fetched.append(self.uri)
        return jwks

    monkeypatch.setattr(pyjwt.PyJWKClient, "fetch_data", fetch_data)
    monkeypatch.setattr(deps, "_jwks_clients", {})
    return deps._decode_jwt(token, SimpleNamespace(supabase_url=url, supabase_jwt_secret=secret)), fetched


def _es_token(key, kid="k1", **claims):
    payload = {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 60, **claims}
    return pyjwt.encode(payload, key, algorithm="ES256", headers={"kid": kid})


def test_es256_token_verified_with_the_projects_jwks(monkeypatch):
    key, jwks = _es256()
    claims, fetched = _decode(_es_token(key), monkeypatch, jwks)
    assert claims["sub"] == "user-1"
    assert fetched == ["https://proj.supabase.co/auth/v1/.well-known/jwks.json"]


def test_es256_token_from_another_key_or_expired_is_rejected(monkeypatch):
    import pytest
    from fastapi import HTTPException

    _, jwks = _es256()
    other, _ = _es256()
    with pytest.raises(HTTPException) as e:
        _decode(_es_token(other), monkeypatch, jwks)
    assert e.value.status_code == 401 and e.value.detail == "Invalid token."
    key, jwks = _es256()
    with pytest.raises(HTTPException) as e:
        _decode(_es_token(key, exp=int(time.time()) - 10), monkeypatch, jwks)
    assert e.value.detail == "Token has expired."


def test_es256_token_without_supabase_url_is_503(monkeypatch):
    import pytest
    from fastapi import HTTPException

    key, jwks = _es256()
    with pytest.raises(HTTPException) as e:
        _decode(_es_token(key), monkeypatch, jwks, url="")
    assert e.value.status_code == 503


def test_hs256_still_uses_the_shared_secret_and_never_fetches_jwks(monkeypatch):
    claims, fetched = _decode(_token("legacy-secret-0123456789abcdefghij"), monkeypatch, {"keys": []},
                              secret="legacy-secret-0123456789abcdefghij")  # fmt: skip
    assert claims["sub"] == "user-1" and fetched == []


# ---- adding a member looks the user up by exact email (Supabase's admin API only has a substring filter) ----


def test_find_user_id_requires_an_exact_email_match(monkeypatch):
    import httpx

    from app import routes_admin

    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(params)
        users = [{"id": "u-ann", "email": "joann@acme.test"}, {"id": "u-an", "email": "ann@acme.test"}]
        return httpx.Response(200, json={"users": users}, request=httpx.Request("GET", url))

    monkeypatch.setattr(routes_admin.httpx, "get", fake_get)
    assert routes_admin.find_user_id("https://p.supabase.co", "k", " Ann@Acme.test ") == "u-an"
    assert seen["filter"] == "ann@acme.test"
    assert routes_admin.find_user_id("https://p.supabase.co", "k", "nn@acme.test") is None
