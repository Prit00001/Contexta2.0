"""Auth dependencies for workspace routes: verifies a Supabase JWT (HMAC check against the
project's JWT secret - no network call) or a workspace API key, then checks the caller's
membership role for the workspace in the path. This is what closes the Phase 1c "no auth"
gap - every /workspaces/{workspace_id}/* route now depends on require_role(...)."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import HTTPException, Request

from app.config import Settings
from core import db
from core.auth import ROLE_RANK

_JWT_ALG = "HS256"


@dataclass
class AuthContext:
    role: str
    user_id: Optional[str] = None
    via: str = "jwt"


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Authorization: Bearer token.")
    return header[7:].strip()


def _lookup_membership_role(conn, user_id: str, workspace_id: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT role FROM memberships WHERE user_id=%s AND workspace_id=%s", (user_id, workspace_id))
        row = cur.fetchone()
    return row[0] if row else None


def _lookup_api_key_role(conn, token: str, workspace_id: str) -> Optional[str]:
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM api_keys WHERE hashed_key=%s AND workspace_id=%s AND revoked_at IS NULL",
            (hashed, workspace_id),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("UPDATE api_keys SET last_used_at=now() WHERE id=%s", (row[0],))
    conn.commit()
    return "member"  # a workspace API key grants a fixed programmatic-access role


def _require_jwt_secret(settings: Settings) -> None:
    # Fail closed: never attempt HMAC verification with an empty key, regardless of what the
    # installed PyJWT version would do with it (defect D-02, SR-31).
    if not settings.supabase_jwt_secret:
        raise HTTPException(503, "Workspace authentication is not configured on this server.")


def _decode_jwt(token: str, settings: Settings) -> dict:
    _require_jwt_secret(settings)
    try:
        claims = jwt.decode(token, settings.supabase_jwt_secret, algorithms=[_JWT_ALG], audience="authenticated")
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token has expired.")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid token.")
    if not claims.get("sub"):
        raise HTTPException(401, "Invalid token.")
    return claims


def require_role(min_role: str):
    """FastAPI dependency factory. workspace_id is picked up automatically from the
    route's path parameter of the same name."""

    async def dependency(workspace_id: str, request: Request) -> AuthContext:
        settings: Settings = request.app.state.settings
        pool = request.app.state.db_pool
        if pool is None:
            raise HTTPException(503, "Database is not configured (DATABASE_URL not set).")
        token = _bearer_token(request)

        def _check() -> AuthContext:
            conn = db.get_pool_conn(pool)
            try:
                if token.count(".") == 2:  # header.payload.signature -> looks like a JWT
                    claims = _decode_jwt(token, settings)
                    user_id = claims.get("sub")
                    role = _lookup_membership_role(conn, user_id, workspace_id)
                    if role is None:
                        raise HTTPException(403, "You are not a member of this workspace.")
                    return AuthContext(role=role, user_id=user_id, via="jwt")
                role = _lookup_api_key_role(conn, token, workspace_id)
                if role is None:
                    raise HTTPException(401, "Invalid or revoked API key.")
                return AuthContext(role=role, via="api_key")
            finally:
                db.put_pool_conn(pool, conn)

        ctx = await asyncio.to_thread(_check)
        if ROLE_RANK[ctx.role] < ROLE_RANK[min_role]:
            raise HTTPException(403, f"Requires '{min_role}' role or higher; you have '{ctx.role}'.")
        return ctx

    return dependency


async def require_user(request: Request) -> str:
    """For routes with no workspace_id yet (e.g. creating one) - a valid user JWT only."""
    token = _bearer_token(request)
    if token.count(".") != 2:
        raise HTTPException(401, "A user token (not an API key) is required here.")
    settings: Settings = request.app.state.settings
    claims = _decode_jwt(token, settings)
    return claims["sub"]
