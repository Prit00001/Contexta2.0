import type { AskResponse, WorkspaceAskResponse } from "../api/types";

export const citationCheck = { verification: "marker_only" as const, valid_markers: 1, removed_markers: [], uncited_claims: 1 };

export const askResponse: AskResponse = {
  question: "What is RAG?",
  answer: "RAG retrieves passages [1]. It is popular.",
  sources: [
    {
      id: "doc_004", title: "Retrieval-Augmented Generation", source: "local", url: "https://arxiv.org/abs/2005.11401",
      score: 0.7, snippet: "RAG combines a retriever with a generator.", authors: ["Lewis", "Perez", "Piktus"],
      published: "2020-05-22", page: null, marker: 1, document_id: "doc_004", chunk_id: "doc_004#0",
    },
  ],
  model_used: "openai/gpt-oss-120b", provider: "groq", llm_used: true, cached: false, diagnostics: [],
  timings_ms: { total: 1234 }, request_id: "r1", outcome: "answered", workflow: [],
  claims: [
    { text: "RAG retrieves passages.", markers: [1], status: "cited" },
    { text: "It is popular.", markers: [], status: "uncited" },
  ],
  citation_check: citationCheck,
};

export const wsAnswer = (over: Partial<WorkspaceAskResponse> = {}): WorkspaceAskResponse => ({
  question: "How many vacation days?",
  answer: "Employees get 20 days [1].",
  sources: [{ document_id: "d1", filename: "policy.md", page_number: null, section_heading: "Vacation", snippet: "Employees get 20 paid vacation days.", score: 0.8 }],
  provider: "groq", model_used: "m", llm_used: true, outcome: "answered",
  claims: [{ text: "Employees get 20 days.", markers: [1], status: "cited" }],
  citation_check: { ...citationCheck, uncited_claims: 0 },
  conversation_id: null,
  ...over,
});

/** A Response whose body streams the given SSE events. */
export function sseResponse(events: [string, unknown][]): Response {
  const text = events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  const bytes = new TextEncoder().encode(text);
  const body = new ReadableStream({
    start(c) {
      // split mid-event to exercise buffering
      c.enqueue(bytes.slice(0, 17));
      c.enqueue(bytes.slice(17));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

export const json = (data: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;

/** Routes fetch calls by "METHOD path" (path without query); records every call. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method || "GET").toUpperCase();
    const path = url.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ method, url: path, body });
    const h = routes[`${method} ${path}`];
    if (!h) return json({ detail: `unmocked ${method} ${path}` }, 404);
    return h(url, init);
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}
