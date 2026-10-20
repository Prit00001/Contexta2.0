"""Minimal Razorpay Subscriptions client (ADR-007): create a per-seat subscription, change its
seat count, cancel it. Basic auth with the API key id and secret; never logged."""

from __future__ import annotations

import logging

import httpx

API = "https://api.razorpay.com/v1"
log = logging.getLogger("contexta.billing")


class RazorpayError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Razorpay:
    def __init__(self, client: httpx.AsyncClient, key_id: str, key_secret: str):
        self.client, self.auth = client, (key_id, key_secret)

    async def _call(self, method: str, path: str, body: dict) -> dict:
        try:
            r = await self.client.request(method, f"{API}{path}", json=body, auth=self.auth, timeout=15.0)
        except httpx.HTTPError as exc:
            raise RazorpayError(f"Payment provider unreachable ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("description") or r.text[:200]
            except ValueError:
                detail = r.text[:200]
            raise RazorpayError(detail, r.status_code)
        return r.json()

    async def create_subscription(self, plan_id: str, seats: int, total_count: int, workspace_id: str) -> dict:
        return await self._call(
            "POST",
            "/subscriptions",
            {
                "plan_id": plan_id,
                "quantity": seats,
                "total_count": total_count,
                "customer_notify": 1,
                "notes": {"workspace_id": workspace_id},
            },
        )

    async def set_quantity(self, subscription_id: str, seats: int) -> dict:
        # At renewal, so nobody is charged mid-cycle. Razorpay refuses updates to UPI-paid subscriptions.
        return await self._call(
            "PATCH", f"/subscriptions/{subscription_id}", {"quantity": seats, "schedule_change_at": "cycle_end"}
        )

    async def cancel(self, subscription_id: str) -> dict:
        return await self._call("POST", f"/subscriptions/{subscription_id}/cancel", {"cancel_at_cycle_end": 1})
