import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import { AuthProvider } from "../../auth";
import { json, mockFetch } from "../../test/fixtures";

const config = { workspaces_enabled: true, supabase_url: "https://proj.supabase.co", supabase_anon_key: "anon", history_retention_days: 30 };
const TOKEN = "aaa.eyJlbWFpbCI6ImFAYi5jbyJ9.ccc"; // payload {"email":"a@b.co"}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/workspace"]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  history.replaceState(null, "", "/workspace");
});
afterEach(() => vi.unstubAllGlobals());

test("sign-up with email confirmation on asks the user to check their email", async () => {
  const calls = mockFetch({
    "GET /auth/config": () => json(config),
    "POST /auth/v1/signup": () => json({ id: "u1", email: "a@b.co" }),
  });
  renderApp();
  await userEvent.click(await screen.findByRole("button", { name: "Create an account" }));
  await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
  await userEvent.type(screen.getByLabelText("Password"), "short");
  await userEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(await screen.findByText(/at least 8 characters/)).toBeInTheDocument();
  expect(calls.some((c) => c.url === "/auth/v1/signup")).toBe(false);

  await userEvent.type(screen.getByLabelText("Password"), "-and-long");
  await userEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(await screen.findByText(/sent a confirmation link to a@b.co/)).toBeInTheDocument();
  expect(calls.find((c) => c.url === "/auth/v1/signup")?.body).toEqual({ email: "a@b.co", password: "short-and-long" });
});

test("sign-up without confirmation signs straight in", async () => {
  mockFetch({
    "GET /auth/config": () => json(config),
    "POST /auth/v1/signup": () => json({ access_token: TOKEN, refresh_token: "r", user: { email: "a@b.co" } }),
    "GET /workspaces": () => json([]),
  });
  renderApp();
  await userEvent.click(await screen.findByRole("button", { name: "Create an account" }));
  await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
  await userEvent.type(screen.getByLabelText("Password"), "long-enough");
  await userEvent.click(screen.getByRole("button", { name: "Create account" }));
  expect(await screen.findByText("Create your first workspace")).toBeInTheDocument();
});

test("a confirmation link in the URL signs the user in and is removed from the URL", async () => {
  history.replaceState(null, "", `/workspace#access_token=${TOKEN}&refresh_token=r&type=signup`);
  mockFetch({ "GET /auth/config": () => json(config), "GET /workspaces": () => json([]) });
  renderApp();
  expect(await screen.findByText("Create your first workspace")).toBeInTheDocument();
  expect(screen.getByText("a@b.co")).toBeInTheDocument();
  expect(window.location.hash).toBe("");
});

test("an expired email link shows why", async () => {
  history.replaceState(null, "", "/workspace#error=access_denied&error_description=Email+link+is+invalid+or+has+expired");
  mockFetch({ "GET /auth/config": () => json(config) });
  renderApp();
  expect(await screen.findByText("Email link is invalid or has expired.")).toBeInTheDocument();
});

test("forgot password, then the reset link asks for a new password before anything else", async () => {
  const calls = mockFetch({
    "GET /auth/config": () => json(config),
    "POST /auth/v1/recover": () => json({}),
    "PUT /auth/v1/user": () => json({ id: "u1" }),
    "GET /workspaces": () => json([]),
  });
  const first = renderApp();
  await userEvent.click(await screen.findByRole("button", { name: "Forgot password?" }));
  await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
  await userEvent.click(screen.getByRole("button", { name: "Send reset link" }));
  expect(await screen.findByText(/link to reset its password is on its way/)).toBeInTheDocument();
  expect(calls.find((c) => c.url === "/auth/v1/recover")?.body).toEqual({ email: "a@b.co" });
  first.unmount();

  history.replaceState(null, "", `/workspace#access_token=${TOKEN}&refresh_token=r&type=recovery`);
  renderApp();
  await userEvent.type(await screen.findByLabelText("New password"), "brand-new-pass");
  await userEvent.click(screen.getByRole("button", { name: "Save password" }));
  expect(await screen.findByText("Create your first workspace")).toBeInTheDocument();
  expect(calls.find((c) => c.method === "PUT" && c.url === "/auth/v1/user")?.body).toEqual({ password: "brand-new-pass" });
});
