import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Billing } from "../../api/types";
import { useAuth } from "../../auth";

const HEARTBEAT_MS = 60_000;
const IDLE_MS = 120_000; // no input for 2 minutes = not active, so an open tab doesn't use the trial

/** The workspace's plan, plus a heartbeat that counts active use toward the free trial (ADR-007):
 *  sent once a minute only while the tab is visible and someone touched it in the last 2 minutes. */
export function useBilling(wsId: string | undefined) {
  const { authFetch } = useAuth();
  const [billing, setBilling] = useState<Billing | null>(null);
  const lastInput = useRef(Date.now());

  const refresh = useCallback(async () => {
    if (!wsId) return;
    try {
      setBilling(await authFetch<Billing>(`/workspaces/${wsId}/billing`));
    } catch {
      /* billing info is a convenience; the server enforces the paywall */
    }
  }, [authFetch, wsId]);

  useEffect(() => {
    setBilling(null);
    void refresh();
  }, [refresh]);

  const metered = billing?.enabled && billing.plan === "trial";
  useEffect(() => {
    if (!wsId || !metered) return;
    const touch = () => (lastInput.current = Date.now());
    const events = ["pointerdown", "keydown", "scroll", "pointermove"] as const;
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible" || Date.now() - lastInput.current > IDLE_MS) return;
      authFetch<Billing>(`/workspaces/${wsId}/activity`, { method: "POST" }).then(setBilling, () => {});
    }, HEARTBEAT_MS);
    return () => {
      clearInterval(timer);
      events.forEach((e) => window.removeEventListener(e, touch));
    };
  }, [wsId, metered, authFetch]);

  return { billing, refresh };
}

export const minutes = (s: number) => Math.max(0, Math.ceil(s / 60));

export function TrialBanner({ billing, wsId }: { billing: Billing | null; wsId: string }) {
  if (!billing?.enabled || billing.plan === "active" || billing.plan === "unlimited") return null;
  const subscribe = billing.can_manage ? (
    <Link to={`/workspace/${wsId}/billing`}>Subscribe</Link>
  ) : (
    <span>Ask a workspace admin to subscribe.</span>
  );
  if (billing.plan === "trial") {
    const left = minutes(billing.trial_seconds_left);
    return (
      <div className={`banner${left <= 10 ? " warn" : ""}`} role="status">
        Free trial: {left} {left === 1 ? "minute" : "minutes"} of active use left. {billing.can_manage && subscribe}
      </div>
    );
  }
  if (billing.plan === "past_due") {
    return (
      <div className="banner warn" role="status">
        A renewal payment failed and Razorpay is retrying it. Questions keep working meanwhile. {billing.can_manage && subscribe}
      </div>
    );
  }
  return (
    <div className="banner stop" role="alert">
      The free trial has ended: documents and saved conversations stay readable, but asking and uploading are paused. {subscribe}
    </div>
  );
}
