-- Workspace billing (ADR-007): a free trial of active use, then a per-seat monthly Razorpay
-- subscription. One row per workspace, created on first activity. Written only by the API
-- (service role); members may read their own workspace's row.

CREATE TABLE workspace_billing (
    workspace_id uuid PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    active_seconds int NOT NULL DEFAULT 0,        -- trial clock: seconds of active use so far
    last_active_at timestamptz,                   -- last heartbeat / request, for gap detection
    razorpay_subscription_id text UNIQUE,
    subscription_status text,                     -- Razorpay's own status (created, authenticated, active, ...)
    seats_billed int,                             -- quantity on the subscription
    current_end timestamptz,                      -- end of the paid period Razorpay reported
    updated_at timestamptz NOT NULL DEFAULT now()
);

GRANT ALL ON workspace_billing TO service_role;
GRANT SELECT ON workspace_billing TO authenticated;

ALTER TABLE workspace_billing ENABLE ROW LEVEL SECURITY;

CREATE POLICY member_read_billing ON workspace_billing
    FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM memberships WHERE user_id = auth.uid()));
