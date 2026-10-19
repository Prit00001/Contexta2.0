"""Workspace billing (ADR-007): a free trial measured in active use, then a per-seat monthly
Razorpay subscription. Sync Postgres access like core/db.py; routes call it via asyncio.to_thread.

Trial clock: every heartbeat or request from a workspace adds the time since the previous one,
but only when that gap is short (``gap_s``) - so an idle tab, a weekend or a closed laptop never
counts, and a burst of requests is not double-counted. The first event after a long gap adds 0.

Entitlement is decided from Razorpay's own subscription status (kept current by the webhook):
``authenticated`` / ``active`` = paid, ``pending`` = a renewal is being retried (grace: still
allowed), anything else (or none) = the trial decides.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

PAID = {"authenticated", "active"}
GRACE = {"pending"}  # Razorpay retries a failed renewal before halting the subscription


@dataclass
class BillingState:
    active_seconds: int = 0
    subscription_id: Optional[str] = None
    subscription_status: Optional[str] = None
    seats_billed: Optional[int] = None
    current_end: Optional[datetime] = None


def plan(state: BillingState, trial_seconds: int) -> str:
    """trial | active | past_due | expired."""
    if state.subscription_status in PAID:
        return "active"
    if state.subscription_status in GRACE:
        return "past_due"
    return "trial" if state.active_seconds < trial_seconds else "expired"


def allowed(state: BillingState, trial_seconds: int) -> bool:
    return plan(state, trial_seconds) != "expired"


_COLUMNS = "active_seconds, razorpay_subscription_id, subscription_status, seats_billed, current_end"


def _state(row) -> BillingState:
    return BillingState(*row) if row else BillingState()


def get_state(conn, workspace_id: str) -> BillingState:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM workspace_billing WHERE workspace_id = %s", (workspace_id,))
        return _state(cur.fetchone())


def record_activity(conn, workspace_id: str, gap_s: int = 120) -> BillingState:
    """Add the time since the last activity to the trial clock, if it was within ``gap_s``."""
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO workspace_billing (workspace_id, last_active_at) VALUES (%(ws)s, now())
                ON CONFLICT (workspace_id) DO UPDATE SET
                  active_seconds = workspace_billing.active_seconds + CASE
                    WHEN workspace_billing.last_active_at > now() - make_interval(secs => %(gap)s)
                    THEN LEAST(EXTRACT(EPOCH FROM now() - workspace_billing.last_active_at), %(gap)s)::int
                    ELSE 0 END,
                  last_active_at = now(), updated_at = now()
                RETURNING {_COLUMNS}""",
            {"ws": workspace_id, "gap": gap_s},
        )
        row = cur.fetchone()
    conn.commit()
    return _state(row)


def save_subscription(conn, workspace_id: str, subscription_id: str, status: str, seats: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO workspace_billing (workspace_id, razorpay_subscription_id, subscription_status, seats_billed)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (workspace_id) DO UPDATE SET razorpay_subscription_id = EXCLUDED.razorpay_subscription_id,
                 subscription_status = EXCLUDED.subscription_status, seats_billed = EXCLUDED.seats_billed,
                 current_end = NULL, updated_at = now()""",
            (workspace_id, subscription_id, status, seats),
        )
    conn.commit()


def update_from_razorpay(
    conn, subscription_id: str, status: str, seats: Optional[int], current_end: Optional[int]
) -> Optional[str]:
    """Apply a subscription entity from Razorpay (webhook). Returns the workspace id, or None when
    the subscription is not one of ours (e.g. replaced by a newer one)."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE workspace_billing SET subscription_status = %s,
                 seats_billed = COALESCE(%s, seats_billed),
                 current_end = COALESCE(to_timestamp(%s), current_end), updated_at = now()
               WHERE razorpay_subscription_id = %s RETURNING workspace_id""",
            (status, seats, current_end, subscription_id),
        )
        row = cur.fetchone()
    conn.commit()
    return str(row[0]) if row else None


def set_seats(conn, workspace_id: str, seats: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE workspace_billing SET seats_billed = %s, updated_at = now() WHERE workspace_id = %s",
            (seats, workspace_id),
        )
    conn.commit()


def member_count(conn, workspace_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM memberships WHERE workspace_id = %s", (workspace_id,))
        return int(cur.fetchone()[0])


# ---- Razorpay signatures (the same HMAC-SHA256 hex digests Razorpay's own SDKs compute) ----


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_payment(payment_id: str, subscription_id: str, signature: str, key_secret: str) -> bool:
    """Checkout success callback: signed ``payment_id|subscription_id`` with the API key secret."""
    expected = _hmac(key_secret, f"{payment_id}|{subscription_id}".encode())
    return hmac.compare_digest(expected, signature or "")


def verify_webhook(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    """``X-Razorpay-Signature``: the raw request body signed with the webhook secret."""
    return hmac.compare_digest(_hmac(webhook_secret, raw_body), signature or "")
