import type { AskResponse, Source, Turn } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** A readable message for a failed response (FastAPI puts it in `detail`). */
export async function errorMessage(r: Response): Promise<string> {
  if (r.status === 429) return "Too many requests. Wait a minute, then try again.";
  if (r.status === 413) return "That file is too large.";
  try {
    const body = await r.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((e: { msg: string }) => e.msg).join("; ");
  } catch {
    /* not JSON */
  }
  return `Something went wrong (${r.status}). Try again in a moment.`;
}

export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) throw new ApiError(await errorMessage(r), r.status);
  return r.json() as Promise<T>;
}

// ---- server-sent events (/ask/stream) ----

export type StreamEvent =
  | { event: "stage"; data: { stage: string; status: string } }
  | { event: "sources"; data: Source[] }
  | { event: "token"; data: string }
  | { event: "reset"; data: { attempt: number } }
  | { event: "done"; data: AskResponse }
  | { event: "error"; data: { status: number; detail: string } };

/** Splits an SSE byte stream into events. Returns the unconsumed tail to prepend to the next chunk. */
export function parseSse(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = [];
  let rest = buffer;
  let cut: number;
  while ((cut = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, cut);
    rest = rest.slice(cut + 2);
    const event = /^event: (.*)$/m.exec(block)?.[1];
    const data = /^data: (.*)$/m.exec(block)?.[1];
    if (event && data !== undefined) events.push({ event, data: JSON.parse(data) } as StreamEvent);
  }
  return { events, rest };
}

export interface AskParams {
  question: string;
  top_k: number;
  use_arxiv: boolean;
  history: Turn[];
}

/** POST /ask/stream; calls onEvent for every event and resolves with the final answer. */
export async function askStream(
  params: AskParams,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<AskResponse> {
  const r = await fetch("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal,
  });
  if (!r.ok || !r.body) throw new ApiError(await errorMessage(r), r.status);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let final: AskResponse | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const { events, rest } = parseSse(buf + decoder.decode(value, { stream: true }));
    buf = rest;
    for (const e of events) {
      if (e.event === "error") throw new ApiError(e.data.detail || "Something went wrong.", e.data.status);
      if (e.event === "done") final = e.data;
      onEvent(e);
    }
  }
  if (!final) throw new ApiError("The answer was interrupted. Try again in a moment.", 0);
  return final;
}

export async function askPdf(question: string, topK: number, file: File): Promise<AskResponse> {
  const fd = new FormData();
  fd.append("question", question);
  fd.append("top_k", String(topK));
  fd.append("file", file);
  return getJson<AskResponse>("/ask-pdf", { method: "POST", body: fd });
}
