import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../../App";
import { AuthProvider } from "../../auth";
import { json, mockFetch, wsAnswer } from "../../test/fixtures";

const WS = { workspace_id: "w1", name: "People Team", role: "admin", org_id: "o1" };
const config = { workspaces_enabled: true, supabase_url: null, supabase_anon_key: null, history_retention_days: 30 };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

test("signs in with a pasted token and lands in the first workspace", async () => {
  const calls = mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json([WS]),
    "GET /workspaces/w1/conversations": () => json([]),
  });
  renderAt("/workspace");
  const token = "aaa.eyJlbWFpbCI6ImFAYi5jbyJ9.ccc"; // payload {"email":"a@b.co"}
  await userEvent.type(await screen.findByLabelText("Access token"), token);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(await screen.findByText("New conversation")).toBeInTheDocument();
  expect(screen.getByText("a@b.co")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Members" })).toBeInTheDocument(); // admin-only tab
  const authed = calls.find((c) => c.url === "/workspaces");
  expect(authed).toBeDefined();
});

test("saving a conversation keeps the thread on screen and lists it; follow-ups continue it", async () => {
  sessionStorage.setItem("contexta.auth", JSON.stringify({ access_token: "a.b.c", email: "a@b.co" }));
  let conversations: unknown[] = [];
  const calls = mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json([WS]),
    "GET /workspaces/w1/conversations": () => json(conversations),
    "POST /workspaces/w1/ask": (_u, init) => {
      const body = JSON.parse(init!.body as string);
      if (body.save) {
        conversations = [{ id: "c1", title: body.question, created_at: "2026-10-19T10:00:00Z", updated_at: new Date().toISOString(), turns: 1 }];
        return json(wsAnswer({ conversation_id: "c1" }));
      }
      return json(wsAnswer({ conversation_id: "c1", standalone_question: "Can unused vacation days carry over?" }));
    },
  });
  renderAt("/workspace/w1/ask");
  await userEvent.type(await screen.findByLabelText("Your question"), "How many vacation days?{Enter}");
  expect(await screen.findByText("Employees get 20 days.")).toBeInTheDocument();
  const sidebar = await screen.findByRole("link", { name: /How many vacation days\?/ });
  expect(sidebar).toHaveClass("active");
  expect(screen.getByText(/Deleted on/)).toBeInTheDocument();
  expect(calls.some((c) => c.url === "/workspaces/w1/conversations/c1")).toBe(false); // no reload flash

  await userEvent.type(screen.getByPlaceholderText("Ask a follow-up…"), "Carry over?{Enter}");
  expect(await screen.findByText("Read as: “Can unused vacation days carry over?”")).toBeInTheDocument();
  const asks = calls.filter((c) => c.url === "/workspaces/w1/ask").map((c) => c.body as Record<string, unknown>);
  expect(asks[0]).toMatchObject({ save: true });
  expect(asks[1]).toMatchObject({ conversation_id: "c1" });
  expect(asks[1]).not.toHaveProperty("save");
});

test("opening a stored conversation shows its full passages", async () => {
  sessionStorage.setItem("contexta.auth", JSON.stringify({ access_token: "a.b.c" }));
  mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json([{ ...WS, role: "viewer" }]),
    "GET /workspaces/w1/conversations": () => json([]),
    "GET /workspaces/w1/conversations/c9": () =>
      json({
        id: "c9", title: "Old thread", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
        expires_at: "2026-10-31T00:00:00Z",
        // exactly what GET /conversations/{id} returns per turn (ConversationTurnOut): no citation_check
        turns: [{
          position: 1, question: "How many vacation days?", standalone_question: null, answer: "Employees get 20 days [1].",
          outcome: "answered", citations: [], created_at: "2026-10-01T00:00:00Z",
          claims: [{ text: "Employees get 20 days.", markers: [1], status: "cited" }, { text: "Ask HR.", markers: [], status: "uncited" }],
          sources: [{ document_id: "d1", filename: "policy.md", page_number: 2, section_heading: null, text: "The full stored passage.", score: 1 }],
        }],
      }),
  });
  renderAt("/workspace/w1/ask/c9");
  expect(await screen.findByRole("heading", { name: "Old thread" })).toBeInTheDocument();
  expect(screen.getByText("1 of 2 sentences cite a source")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /1 source/ }));
  const item = screen.getByText("policy.md").closest("li")!;
  expect(within(item).getByText("Page 2")).toBeInTheDocument();
  expect(within(item).getByText("The full stored passage.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Members" })).toBeNull(); // viewer
});

test("an expired session returns to sign-in with a notice", async () => {
  sessionStorage.setItem("contexta.auth", JSON.stringify({ access_token: "a.b.c" }));
  mockFetch({
    "GET /auth/config": () => json(config),
    "GET /workspaces": () => json({ detail: "Token expired" }, 401),
  });
  renderAt("/workspace");
  expect(await screen.findByText("Your session expired. Sign in again.")).toBeInTheDocument();
  await waitFor(() => expect(sessionStorage.getItem("contexta.auth")).toBeNull());
});
