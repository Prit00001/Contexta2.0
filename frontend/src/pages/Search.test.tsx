import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Search } from "./Search";
import { askResponse, json, mockFetch, sseResponse } from "../test/fixtures";

afterEach(() => vi.unstubAllGlobals());

test("streams an answer, then sends it as history with the follow-up", async () => {
  const calls = mockFetch({
    "GET /health": () => json({ status: "ok", index_size: 25, llm_providers: ["groq"], arxiv_enabled: true }),
    "POST /ask/stream": () =>
      sseResponse([
        ["stage", { stage: "retrieve", status: "ok" }],
        ["sources", askResponse.sources],
        ["token", "RAG retrieves "],
        ["done", askResponse],
      ]),
  });
  render(<MemoryRouter><Search /></MemoryRouter>);
  await userEvent.type(screen.getByLabelText("Your question"), "What is RAG?{Enter}");
  expect(await screen.findByText("RAG retrieves passages.")).toBeInTheDocument();
  expect(screen.getByText("gpt-oss-120b · 1.2s")).toBeInTheDocument();
  expect(await screen.findByText(/25 library papers/)).toBeInTheDocument();

  await userEvent.type(screen.getByPlaceholderText("Ask a follow-up…"), "And its limits?{Enter}");
  await waitFor(() => expect(calls.filter((c) => c.url === "/ask/stream")).toHaveLength(2));
  const second = calls.filter((c) => c.url === "/ask/stream")[1].body as { history: unknown[] };
  expect(second.history).toEqual([{ question: "What is RAG?", answer: askResponse.answer }]);

  await userEvent.click(screen.getByRole("button", { name: "New topic" }));
  expect(screen.getByPlaceholderText("Ask about machine learning or NLP…")).toBeInTheDocument();
});

test("shows the server's error message", async () => {
  mockFetch({
    "GET /health": () => json({}, 500),
    "POST /ask/stream": () => json({ detail: "Too many requests" }, 429),
  });
  render(<MemoryRouter><Search /></MemoryRouter>);
  await userEvent.type(screen.getByLabelText("Your question"), "What is RAG?{Enter}");
  expect(await screen.findByText("Too many requests. Wait a minute, then try again.")).toBeInTheDocument();
});
