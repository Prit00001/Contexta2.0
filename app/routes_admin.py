"""Workspace bootstrap, membership, and API-key management. Separate from
app/routes_workspace.py (document/Q&A endpoints) to keep concerns apart."""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import require_role, require_user
from app.schemas import (
    AddMemberRequest,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyOut,
    CreateWorkspaceRequest,
    CreateWorkspaceResponse,
    MemberOut,
)
from core import auth, db

router = APIRouter()


def _pool(request: Request):
    pool = request.app.state.db_pool
    if pool is None:
        raise HTTPException(503, "Database is not configured (DATABASE_URL not set).")
    return pool


@router.post("/workspaces", response_model=CreateWorkspaceResponse)
async def create_workspace(body: CreateWorkspaceRequest, request: Request, user_id: str = Depends(require_user)):
    pool = _pool(request)

    def _create():
        conn = db.get_pool_conn(pool)
        try:
            return db.create_org_workspace(
                conn, org_name=body.org_name, workspace_name=body.workspace_name, admin_user_id=user_id,
            )
        finally:
            db.put_pool_conn(pool, conn)

    org_id, ws_id = await asyncio.to_thread(_create)
    return CreateWorkspaceResponse(org_id=org_id, workspace_id=ws_id)


@router.post("/workspaces/{workspace_id}/members", status_code=204)
async def add_member(workspace_id: str, body: AddMemberRequest, request: Request, ctx=Depends(require_role("admin"))):
    settings = request.app.state.settings
    if not (settings.supabase_url and settings.supabase_service_key):
        raise HTTPException(503, "SUPABASE_URL/SUPABASE_SERVICE_KEY are not configured.")
    pool = _pool(request)

    def _add():
        r = httpx.get(
            f"{settings.supabase_url}/auth/v1/admin/users",
            params={"email": body.email},
            headers={"Authorization": f"Bearer {settings.supabase_service_key}", "apikey": settings.supabase_service_key},
            timeout=10.0,
        )
        r.raise_for_status()
        users = r.json().get("users", [])
        if not users:
            raise HTTPException(404, f"No signed-up user found with email {body.email}.")
        conn = db.get_pool_conn(pool)
        try:
            db.add_membership(conn, workspace_id=workspace_id, user_id=users[0]["id"], role=body.role)
        finally:
            db.put_pool_conn(pool, conn)

    await asyncio.to_thread(_add)


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
async def list_members(workspace_id: str, request: Request, ctx=Depends(require_role("viewer"))):
    pool = _pool(request)

    def _list():
        conn = db.get_pool_conn(pool)
        try:
            return db.list_memberships(conn, workspace_id)
        finally:
            db.put_pool_conn(pool, conn)

    rows = await asyncio.to_thread(_list)
    return [MemberOut(user_id=str(r[0]), role=r[1], created_at=r[2].isoformat()) for r in rows]


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(workspace_id: str, user_id: str, request: Request, ctx=Depends(require_role("admin"))):
    pool = _pool(request)

    def _remove():
        conn = db.get_pool_conn(pool)
        try:
            db.remove_membership(conn, workspace_id=workspace_id, user_id=user_id)
        finally:
            db.put_pool_conn(pool, conn)

    await asyncio.to_thread(_remove)


@router.post("/workspaces/{workspace_id}/api-keys", response_model=ApiKeyCreateResponse)
async def create_api_key(
    workspace_id: str, body: ApiKeyCreateRequest, request: Request, ctx=Depends(require_role("admin"))
):
    pool = _pool(request)
    raw_key = auth.generate_api_key()
    hashed = auth.hash_api_key(raw_key)

    def _create():
        conn = db.get_pool_conn(pool)
        try:
            return db.create_api_key(conn, workspace_id=workspace_id, name=body.name, hashed_key=hashed, created_by=ctx.user_id)
        finally:
            db.put_pool_conn(pool, conn)

    row = await asyncio.to_thread(_create)
    return ApiKeyCreateResponse(id=str(row[0]), name=body.name, api_key=raw_key)


@router.get("/workspaces/{workspace_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(workspace_id: str, request: Request, ctx=Depends(require_role("admin"))):
    pool = _pool(request)

    def _list():
        conn = db.get_pool_conn(pool)
        try:
            return db.list_api_keys(conn, workspace_id)
        finally:
            db.put_pool_conn(pool, conn)

    rows = await asyncio.to_thread(_list)
    return [
        ApiKeyOut(
            id=str(r[0]), name=r[1], created_at=r[2].isoformat(),
            last_used_at=r[3].isoformat() if r[3] else None, revoked_at=r[4].isoformat() if r[4] else None,
        )
        for r in rows
    ]


@router.delete("/workspaces/{workspace_id}/api-keys/{key_id}", status_code=204)
async def revoke_api_key(workspace_id: str, key_id: str, request: Request, ctx=Depends(require_role("admin"))):
    pool = _pool(request)

    def _revoke():
        conn = db.get_pool_conn(pool)
        try:
            ok = db.revoke_api_key(conn, workspace_id, key_id)
            if not ok:
                raise HTTPException(404, "API key not found or already revoked.")
        finally:
            db.put_pool_conn(pool, conn)

    await asyncio.to_thread(_revoke)
