"""Workspace billing (ADR-007): trial status, the activity heartbeat, Razorpay subscribe / verify /
cancel, and Razorpay's webhook. With BILLING_ENABLED off, every workspace is unlimited and these
routes only report that."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import AuthContext, require_role
from app.schemas import BillingOut, SubscribeOut, VerifyPaymentRequest
from core import billing, db
from core.razorpay import Razorpay, RazorpayError

router = APIRouter()
log = logging.getLogger("contexta.billing")

PAYWALL = (
    "This workspace's free trial has ended. A workspace admin can subscribe under Billing to keep asking "
    "questions and uploading documents; everything already here stays readable."
)


async def _in_thread(request: Request, fn, *args, **kwargs):
    pool = request.app.state.db_pool

    def _run():
        conn = db.get_pool_conn(pool)
        try:
            return fn(conn, *args, **kwargs)
        finally:
            db.put_pool_conn(pool, conn)

    return await asyncio.to_thread(_run)


def _trial_seconds(request: Request) -> int:
    return request.app.state.settings.billing_trial_minutes * 60


def _razorpay(request: Request) -> Razorpay:
    s = request.app.state.settings
    return Razorpay(request.app.state.http, s.razorpay_key_id, s.razorpay_key_secret)


async def charge_activity(request: Request, workspace_id: str) -> None:
    """Count this request as active use and refuse it (402) once the trial is over and nothing is
    paid. Called by the workspace routes that cost money to serve: asking and uploading."""
    if not request.app.state.settings.billing_enabled:
        return
    state = await _in_thread(request, billing.record_activity, workspace_id)
    if not billing.allowed(state, _trial_seconds(request)):
        raise HTTPException(402, PAYWALL)


async def _summary(request: Request, workspace_id: str, state: billing.BillingState, ctx: AuthContext) -> BillingOut:
    s = request.app.state.settings
    if not s.billing_enabled:
        return BillingOut(enabled=False, plan="unlimited")
    total = _trial_seconds(request)
    seats = await _in_thread(request, billing.member_count, workspace_id)
    return BillingOut(
        enabled=True,
        plan=billing.plan(state, total),
        trial_seconds_left=max(0, total - state.active_seconds),
        trial_seconds_total=total,
        seats=seats,
        seats_billed=state.seats_billed,
        subscription_status=state.subscription_status,
        current_end=state.current_end.isoformat() if state.current_end else None,
        price_label=s.billing_price_label,
        can_manage=ctx.role == "admin" and ctx.via == "jwt",
    )


@router.get("/workspaces/{workspace_id}/billing", response_model=BillingOut)
async def get_billing(workspace_id: str, request: Request, ctx: AuthContext = Depends(require_role("viewer"))):
    state = await _in_thread(request, billing.get_state, workspace_id)
    return await _summary(request, workspace_id, state, ctx)


@router.post("/workspaces/{workspace_id}/activity", response_model=BillingOut)
async def heartbeat(workspace_id: str, request: Request, ctx: AuthContext = Depends(require_role("viewer"))):
    """Sent by the app about once a minute while someone is actively using a workspace."""
    if not request.app.state.settings.billing_enabled:
        return BillingOut(enabled=False, plan="unlimited")
    state = await _in_thread(request, billing.record_activity, workspace_id)
    return await _summary(request, workspace_id, state, ctx)


@router.post("/workspaces/{workspace_id}/billing/subscribe", response_model=SubscribeOut)
async def subscribe(workspace_id: str, request: Request, ctx: AuthContext = Depends(require_role("admin"))):
    s = request.app.state.settings
    if not s.billing_enabled:
        raise HTTPException(404, "Billing is not enabled on this server.")
    if ctx.via != "jwt":
        raise HTTPException(403, "Subscribe as a signed-in admin, not with an API key.")
    state = await _in_thread(request, billing.get_state, workspace_id)
    if state.subscription_status in billing.PAID | billing.GRACE:
        raise HTTPException(409, "This workspace already has an active subscription.")
    seats = max(1, await _in_thread(request, billing.member_count, workspace_id))
    try:
        sub = await _razorpay(request).create_subscription(
            s.razorpay_plan_id, seats, s.billing_total_count, workspace_id
        )
    except RazorpayError as exc:
        log.warning("razorpay create failed: %s", exc, extra={"event": "billing.subscribe", "status": "error"})
        raise HTTPException(502, f"The payment provider refused the request: {exc}") from exc
    await _in_thread(request, billing.save_subscription, workspace_id, sub["id"], sub.get("status", "created"), seats)
    return SubscribeOut(subscription_id=sub["id"], key_id=s.razorpay_key_id, seats=seats)


@router.post("/workspaces/{workspace_id}/billing/verify", response_model=BillingOut)
async def verify(
    workspace_id: str, body: VerifyPaymentRequest, request: Request, ctx: AuthContext = Depends(require_role("admin"))
):
    """Checkout's success callback. The signature proves Razorpay authorised this subscription; the
    webhook then keeps the status current (charged, halted, cancelled...)."""
    s = request.app.state.settings
    if not s.billing_enabled:
        raise HTTPException(404, "Billing is not enabled on this server.")
    state = await _in_thread(request, billing.get_state, workspace_id)
    if body.razorpay_subscription_id != state.subscription_id:
        raise HTTPException(400, "That payment is for a different subscription.")
    if not billing.verify_payment(
        body.razorpay_payment_id, body.razorpay_subscription_id, body.razorpay_signature, s.razorpay_key_secret
    ):
        raise HTTPException(400, "Payment signature did not verify.")
    if state.subscription_status not in billing.PAID:  # the webhook may already have marked it active
        await _in_thread(request, billing.update_from_razorpay, state.subscription_id, "authenticated", None, None)
    state = await _in_thread(request, billing.get_state, workspace_id)
    return await _summary(request, workspace_id, state, ctx)


@router.post("/workspaces/{workspace_id}/billing/cancel", response_model=BillingOut)
async def cancel(workspace_id: str, request: Request, ctx: AuthContext = Depends(require_role("admin"))):
    """Cancels at the end of the paid period; the webhook records it when it happens."""
    if not request.app.state.settings.billing_enabled:
        raise HTTPException(404, "Billing is not enabled on this server.")
    state = await _in_thread(request, billing.get_state, workspace_id)
    if not state.subscription_id or state.subscription_status not in billing.PAID | billing.GRACE:
        raise HTTPException(409, "There is no active subscription to cancel.")
    try:
        await _razorpay(request).cancel(state.subscription_id)
    except RazorpayError as exc:
        raise HTTPException(502, f"The payment provider refused the request: {exc}") from exc
    return await _summary(request, workspace_id, state, ctx)


async def sync_seats(request: Request, workspace_id: str) -> None:
    """After a member joins or leaves: bill the new seat count from the next renewal. Best effort -
    Razorpay refuses changes to UPI-paid subscriptions, so a failure is logged, never fatal."""
    s = request.app.state.settings
    if not s.billing_enabled:
        return
    state = await _in_thread(request, billing.get_state, workspace_id)
    if not state.subscription_id or state.subscription_status not in billing.PAID | billing.GRACE:
        return
    seats = max(1, await _in_thread(request, billing.member_count, workspace_id))
    if seats == state.seats_billed:
        return
    try:
        await _razorpay(request).set_quantity(state.subscription_id, seats)
        await _in_thread(request, billing.set_seats, workspace_id, seats)
    except RazorpayError as exc:
        log.warning("seat sync failed for a workspace: %s", exc, extra={"event": "billing.seats", "status": "error"})


@router.post("/billing/razorpay/webhook", include_in_schema=False)
async def razorpay_webhook(request: Request):
    s = request.app.state.settings
    if not s.billing_enabled:
        raise HTTPException(404, "Billing is not enabled on this server.")
    raw = await request.body()
    if not billing.verify_webhook(raw, request.headers.get("x-razorpay-signature", ""), s.razorpay_webhook_secret):
        raise HTTPException(400, "Invalid signature.")
    try:
        event = json.loads(raw)
        sub = event["payload"]["subscription"]["entity"]
    except (ValueError, KeyError, TypeError):
        return {"ok": True, "ignored": True}  # signed, but not a subscription event
    workspace_id = await _in_thread(
        request,
        billing.update_from_razorpay,
        sub["id"],
        sub.get("status", ""),
        sub.get("quantity"),
        sub.get("current_end"),
    )
    log.info(
        "razorpay %s -> %s",
        event.get("event"),
        sub.get("status"),
        extra={"event": "billing.webhook", "status": "ok" if workspace_id else "unknown_subscription"},
    )
    return {"ok": True}
