import { useState } from "react";
import type { Billing } from "../../api/types";
import { useAuth } from "../../auth";
import { minutes } from "./billing";
import { useWorkspace } from "./WorkspaceLayout";

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open(): void };
  }
}

const CHECKOUT_JS = "https://checkout.razorpay.com/v1/checkout.js";

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = CHECKOUT_JS;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Could not load Razorpay Checkout. Check your connection and try again."));
    document.head.appendChild(s);
  });
}

const PLAN_TEXT: Record<Billing["plan"], string> = {
  unlimited: "Billing is off on this server: everything is unlimited.",
  trial: "Free trial",
  active: "Subscribed",
  past_due: "Payment being retried",
  expired: "Trial ended",
};

export function BillingView() {
  const { ws, base, billing, refreshBilling } = useWorkspace();
  const { authFetch, session } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!billing) return null;

  const subscribe = async () => {
    setBusy(true);
    setError(null);
    try {
      const { subscription_id, key_id } = await authFetch<{ subscription_id: string; key_id: string }>(
        `${base}/billing/subscribe`,
        { method: "POST" },
      );
      await loadCheckout();
      const checkout = new window.Razorpay!({
        key: key_id,
        subscription_id,
        name: "Contexta",
        description: `${ws.name}: ${billing.seats} ${billing.seats === 1 ? "seat" : "seats"}`,
        prefill: { email: session?.email ?? "" },
        notes: { workspace: ws.name },
        theme: { color: "#4f46e5" },
        handler: async (resp: Record<string, string>) => {
          try {
            await authFetch(`${base}/billing/verify`, {
              method: "POST",
              json: {
                razorpay_payment_id: resp.razorpay_payment_id,
                razorpay_subscription_id: resp.razorpay_subscription_id,
                razorpay_signature: resp.razorpay_signature,
              },
            });
            await refreshBilling();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        },
        modal: { ondismiss: () => setBusy(false) },
      });
      checkout.open();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!confirm("Cancel the subscription at the end of the current paid period?")) return;
    try {
      await authFetch(`${base}/billing/cancel`, { method: "POST" });
      await refreshBilling();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const canSubscribe = billing.enabled && (billing.plan === "trial" || billing.plan === "expired");
  return (
    <div className="inner">
      <div className="page-head">
        <h1>Billing</h1>
      </div>
      <table className="list facts">
        <tbody>
          <tr>
            <th scope="row">Plan</th>
            <td>
              <span className={`pill ${billing.plan === "active" ? "active" : billing.plan === "expired" ? "failed" : ""}`}>
                {PLAN_TEXT[billing.plan]}
              </span>
            </td>
          </tr>
          {billing.plan === "trial" && (
            <tr>
              <th scope="row">Trial left</th>
              <td>
                {minutes(billing.trial_seconds_left)} of {minutes(billing.trial_seconds_total)} minutes of active use
              </td>
            </tr>
          )}
          {billing.enabled && (
            <tr>
              <th scope="row">Seats</th>
              <td>
                {billing.seats} {billing.seats === 1 ? "member" : "members"}
                {billing.seats_billed != null && billing.seats_billed !== billing.seats && (
                  <span className="muted"> ({billing.seats_billed} billed now; the change applies at renewal)</span>
                )}
              </td>
            </tr>
          )}
          {billing.price_label && (
            <tr>
              <th scope="row">Price</th>
              <td>{billing.price_label}</td>
            </tr>
          )}
          {billing.current_end && (
            <tr>
              <th scope="row">Paid until</th>
              <td>{new Date(billing.current_end).toLocaleDateString()}</td>
            </tr>
          )}
        </tbody>
      </table>
      <p className="small muted">
        The trial counts only time someone is actively using this workspace. Paper search stays free. Each member is one
        seat; adding or removing members changes the bill from the next renewal.
      </p>
      {canSubscribe && (
        <button className="btn" disabled={busy} onClick={subscribe}>
          {busy ? "Opening checkout…" : `Subscribe for ${billing.seats} ${billing.seats === 1 ? "seat" : "seats"}`}
        </button>
      )}
      {(billing.plan === "active" || billing.plan === "past_due") && (
        <button className="ghost danger" onClick={cancel}>
          Cancel subscription
        </button>
      )}
      {error && <p className="err">{error}</p>}
    </div>
  );
}
