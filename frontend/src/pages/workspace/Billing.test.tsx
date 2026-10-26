import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import type { Billing } from "../../api/types";
import { AuthProvider } from "../../auth";
import { json, mockFetch } from "../../test/fixtures";

const config = { workspaces_enabled: true, supabase_url: null, supabase_anon_key: null, history_retention_days: 30 };
const admin = { workspace_id: "w1", name: "People Team", role: "admin", org_id: "o1" };
const bill = (over: Partial<Billing>): Billing => ({
  enabled: true, plan: "trial", trial_seconds_left: 2520, trial_seconds_total: 3600, seats: 3, seats_billed: null,
  subscription_status: null, current_end: null, price_label: "₹499 per seat / month", can_manage: true, ...over,
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => sessionStorage.setItem("contexta.auth", JSON.stringify({ access_token: "a.b.c", email: "a@b.co" })));
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.Razorpay;
});

test("the trial banner shows minutes left and links admins to Billing", async () => {
  mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json([admin]),
    "GET /workspaces/w1/conversations": () => json([]),
    "GET /workspaces/w1/billing": () => json(bill({})),
  });
  renderAt("/workspace/w1/ask");
  expect(await screen.findByText(/Free trial: 42 minutes of active use left/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Billing" })).toBeInTheDocument();
});

test("an ended trial tells members it is paused and to ask an admin", async () => {
  mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json([{ ...admin, role: "viewer" }]),
    "GET /workspaces/w1/conversations": () => json([]),
    "GET /workspaces/w1/billing": () => json(bill({ plan: "expired", trial_seconds_left: 0, can_manage: false })),
  });
  renderAt("/workspace/w1/ask");
  expect(await screen.findByRole("alert")).toHaveTextContent("asking and uploading are paused. Ask a workspace admin to subscribe.");
  expect(screen.queryByRole("link", { name: "Billing" })).toBeNull();
});

test("subscribe opens Razorpay Checkout and verifies the payment", async () => {
  let current = bill({ plan: "expired", trial_seconds_left: 0 });
  const calls = mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json([admin]),
    "GET /workspaces/w1/conversations": () => json([]),
    "GET /workspaces/w1/billing": () => json(current),
    "POST /workspaces/w1/billing/subscribe": () => json({ subscription_id: "sub_1", key_id: "rzp_test_key", seats: 3 }),
    "POST /workspaces/w1/billing/verify": () => {
      current = bill({ plan: "active", seats_billed: 3, subscription_status: "authenticated" });
      return json(current);
    },
  });
  let options: Record<string, any> = {};
  window.Razorpay = class {
    constructor(o: Record<string, unknown>) {
      options = o;
    }
    open() {
      void (options.handler as (r: Record<string, string>) => void)({
        razorpay_payment_id: "pay_1", razorpay_subscription_id: "sub_1", razorpay_signature: "sig",
      });
    }
  };
  renderAt("/workspace/w1/billing");
  await userEvent.click(await screen.findByRole("button", { name: "Subscribe for 3 seats" }));
  expect(await screen.findByText("Subscribed")).toBeInTheDocument();
  expect(options).toMatchObject({ key: "rzp_test_key", subscription_id: "sub_1", name: "Contexta" });
  const verify = calls.find((c) => c.url === "/workspaces/w1/billing/verify");
  expect(verify?.body).toEqual({ razorpay_payment_id: "pay_1", razorpay_subscription_id: "sub_1", razorpay_signature: "sig" });
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull()); // banner gone once paid
});
