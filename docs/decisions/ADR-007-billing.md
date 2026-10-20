# ADR-007: Workspace billing — a trial of active use, then per-seat Razorpay subscriptions

| | |
|---|---|
| Status | Accepted — pricing model chosen by the project owner, 2026-10-19 |
| Date | 2026-10-19 |
| Scope | Workspaces (`/workspaces/...`). Paper search (`/ask`, `/ask-pdf`) stays free |

## Context

Contexta is moving from a demo to a product. The owner chose (2026-10-19): workspaces are free for
**one hour of active use**, then need a **per-seat monthly subscription** paid through **Razorpay**
(Indian customers: UPI, cards, netbanking); **paper search stays free** as the shop window.

## Decision

1. **Off unless configured.** `BILLING_ENABLED=false` by default: every workspace is unlimited, so
   self-hosting and development never hit a paywall. Turning it on requires `RAZORPAY_KEY_ID`,
   `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` and `RAZORPAY_PLAN_ID` (checked at startup).
2. **The trial clock counts active use only.** Each workspace has `active_seconds`. The app sends a
   heartbeat (`POST /workspaces/{id}/activity`) once a minute, but only while its tab is visible and
   someone touched it in the last two minutes; every question and upload counts too. Each event adds
   the time since the previous one only if that gap was under two minutes, so idle tabs, nights and
   closed laptops add nothing. `BILLING_TRIAL_MINUTES` (default 60) sets the allowance. The clock is
   per workspace, shared by its members.
3. **What the paywall blocks.** After the trial, with nothing paid: asking and uploading return
   **402** with a message naming the fix. Everything already there stays readable (documents,
   conversations, members) and admins can still manage the workspace. API-key calls count and are
   blocked the same way.
4. **Per-seat subscriptions.** An admin subscribes from the Billing page: the API creates a Razorpay
   subscription on `RAZORPAY_PLAN_ID` with `quantity` = the current member count, and the page opens
   Razorpay Checkout. Checkout's success callback is verified server-side (HMAC-SHA256 of
   `payment_id|subscription_id` with the key secret) before the workspace counts as paid.
5. **Razorpay is the source of truth.** Its signed webhook (`POST /billing/razorpay/webhook`,
   `X-Razorpay-Signature` = HMAC-SHA256 of the raw body with the webhook secret) updates the stored
   subscription status, seats and paid-until date. Entitlement follows Razorpay's status, whichever
   event delivered it: `authenticated` / `active` = paid; `pending` (a renewal being retried) = grace,
   still allowed with a warning; `halted`, `cancelled`, `completed`, `expired` = the paywall returns.
6. **Seat changes apply at renewal.** Adding or removing a member updates the subscription's
   quantity with `schedule_change_at: cycle_end`, so nobody is charged mid-cycle. Razorpay refuses
   quantity changes on UPI-paid subscriptions; that is logged, never blocks the membership change,
   and the Billing page shows members vs. seats billed.
7. **Cancel** (admin) cancels at the end of the paid period; the webhook records it.

## Consequences

- New table `workspace_billing` (migration `0004_billing.sql`); members can read their workspace's row.
- Prices live in Razorpay (the plan); the app only shows `BILLING_PRICE_LABEL`. Changing price = a
  new plan id.
- Not built: invoices/receipts in the app (Razorpay emails them), GST invoice details, proration,
  coupons, a free tier for workspaces beyond the trial. Seat counts for UPI subscriptions can drift
  until the customer re-subscribes.
- Verified against Razorpay's API docs and official SDK for endpoints and signatures, and end to end
  with Razorpay mocked; **not yet run against a live Razorpay test account** (needs the owner's keys).
