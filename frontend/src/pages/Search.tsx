import { useEffect, useRef, useState, type FormEvent } from "react";
import { askPdf, askStream, getJson } from "../api/client";
import type { AskResponse, Health, Source, Turn } from "../api/types";
import { AnswerView, Skeleton, type SourceView } from "../components/Answer";

const MAX_PDF = 4 * 1024 * 1024;
const EXAMPLES: Record<string, string> = {
  "how RAG works": "How does retrieval-augmented generation work?",
  "why LoRA is cheap": "What makes LoRA cheaper than full fine-tuning?",
};

const fmtSize = (b: number) =>
  b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`;

function sourceMeta(s: Source): string {
  if (s.source === "pdf") return `Page ${s.page ?? "?"}`;
  const who = s.authors.length > 2 ? `${s.authors.slice(0, 2).join(", ")} et al.` : s.authors.join(", ");
  return [s.source === "arxiv" ? "arXiv" : "Library", (s.published || "").slice(0, 4), who].filter(Boolean).join(" · ");
}

export const toViews = (sources: Source[]): SourceView[] =>
  sources.map((s) => ({
    title: s.source === "pdf" ? s.title.replace(/ · p\. \d+$/, "") : s.title,
    url: s.url || undefined,
    meta: sourceMeta(s),
    text: s.snippet,
  }));

type View =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "draft"; sources: Source[]; text: string }
  | { kind: "done"; data: AskResponse }
  | { kind: "error"; message: string };

export function Search() {
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [useArxiv, setUseArxiv] = useState(true);
  const [pdf, setPdf] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [view, setView] = useState<View>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  // Follow-ups: the last 3 turns live only in this tab's memory and are sent with the next question.
  const [history, setHistory] = useState<Turn[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const qRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getJson<Health>("/health").then(setHealth).catch(() => {});
    return () => abortRef.current?.abort();
  }, []);

  // Shareable links: /?q=… asks that question on load (once - StrictMode mounts effects twice in dev).
  const asked = useRef(false);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("q")?.trim();
    if (!q || asked.current) return;
    asked.current = true;
    void submit(undefined, q.slice(0, 1000));
  }, []);

  const choosePdf = (f: File | null) => {
    if (f) {
      if (!(f.type === "application/pdf" || /\.pdf$/i.test(f.name))) return setView({ kind: "error", message: "Only PDF files are supported." });
      if (f.size > MAX_PDF) return setView({ kind: "error", message: `That PDF is ${fmtSize(f.size)}. The limit is 4 MB.` });
    }
    setPdf(f);
    qRef.current?.focus();
  };

  const submit = async (e?: FormEvent, override?: string) => {
    e?.preventDefault();
    const q = (override ?? question).trim();
    if (q.length < 3 || busy) return;
    if (override) setQuestion(override);
    if (!pdf && history.length === 0) window.history.replaceState(null, "", `/?q=${encodeURIComponent(q)}`); // shareable
    setBusy(true);
    setView({ kind: "loading" });
    abortRef.current?.abort();
    const ctl = new AbortController();
    abortRef.current = ctl;
    try {
      if (pdf) {
        setView({ kind: "done", data: await askPdf(q, topK, pdf) });
      } else {
        let sources: Source[] = [];
        let text = "";
        const data = await askStream({ question: q, top_k: topK, use_arxiv: useArxiv, history }, (ev) => {
          if (ev.event === "sources") sources = ev.data;
          else if (ev.event === "token") text += ev.data;
          else if (ev.event === "reset") text = "";
          else return;
          setView({ kind: "draft", sources, text });
        }, ctl.signal);
        setView({ kind: "done", data });
        data.diagnostics?.forEach((m) => console.warn("[contexta]", m));
        if (data.outcome !== "insufficient_evidence") setHistory((h) => [...h, { question: q, answer: data.answer.slice(0, 4000) }].slice(-3));
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      const message = err instanceof TypeError ? "Could not reach the server. Check your connection and try again." : (err as Error).message;
      setView({ kind: "error", message });
    } finally {
      setBusy(false);
    }
  };

  const newTopic = () => {
    setHistory([]);
    qRef.current?.focus();
  };

  const placeholder = pdf ? "Ask about this PDF…" : history.length ? "Ask a follow-up…" : "Ask about machine learning or NLP…";

  return (
    <div className="wrap">
      <header className="hero">
        <h1>Cited answers from research papers</h1>
        <p className="lede">Ask about ML and NLP papers, or about a PDF you upload.</p>
      </header>

      <form onSubmit={submit}>
        <div
          className={`box${drag ? " drag" : ""}`}
          onDragEnter={(e) => (e.preventDefault(), setDrag(true))}
          onDragOver={(e) => (e.preventDefault(), setDrag(true))}
          onDragLeave={(e) => (e.preventDefault(), setDrag(false))}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const f = e.dataTransfer.files[0];
            if (f) choosePdf(f);
          }}
        >
          <textarea
            ref={qRef}
            rows={2}
            maxLength={500}
            value={question}
            placeholder={placeholder}
            aria-label="Your question"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
          />
          <div className="row">
            {pdf ? (
              <span className="chip">
                <span>{pdf.name}</span>
                <small>{fmtSize(pdf.size)}</small>
                <button type="button" className="iconbtn" style={{ width: 20, height: 20 }} aria-label="Remove PDF" onClick={() => choosePdf(null)}>
                  ×
                </button>
              </span>
            ) : (
              <button type="button" className="iconbtn" aria-label="Attach a PDF" title="Attach a PDF (max 4 MB)" onClick={() => fileRef.current?.click()}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m21.4 11.1-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
                </svg>
              </button>
            )}
            <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => { choosePdf(e.target.files?.[0] ?? null); e.target.value = ""; }} />
            {history.length > 0 && !pdf && (
              <button type="button" className="ghost" style={{ marginLeft: "auto" }} title="Forget the conversation so far" onClick={newTopic}>
                New topic
              </button>
            )}
            <button className="btn" disabled={busy} style={history.length > 0 && !pdf ? { marginLeft: 0 } : undefined}>
              {busy ? (pdf ? "Reading…" : "Searching…") : "Ask"}
            </button>
          </div>
        </div>
        <div className="opts">
          {!pdf && (
            <label>
              <input type="checkbox" checked={useArxiv} onChange={(e) => setUseArxiv(e.target.checked)} /> Live arXiv
            </label>
          )}
          <label>
            Sources{" "}
            <select value={topK} onChange={(e) => setTopK(Number(e.target.value))}>
              {[3, 5, 8].map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          {!pdf && (
            <span className="try">
              Try{" "}
              {Object.entries(EXAMPLES).map(([label, q], i) => (
                <span key={label}>
                  {i > 0 && " · "}
                  <button type="button" onClick={() => void submit(undefined, q)}>
                    {label}
                  </button>
                </span>
              ))}
            </span>
          )}
        </div>
      </form>

      <section className="result" aria-live="polite">
        <Result view={view} />
      </section>

      {health && (
        <footer className="footer">
          {health.index_size} library papers · {health.llm_providers.length ? health.llm_providers.join(" → ") : "no AI model configured"}
        </footer>
      )}
    </div>
  );
}

function Result({ view }: { view: View }) {
  switch (view.kind) {
    case "idle":
      return null;
    case "loading":
      return <Skeleton />;
    case "error":
      return <p className="err">{view.message}</p>;
    case "draft":
      return (
        <AnswerView
          status="Writing…"
          data={{ answer: view.text, outcome: "answered", claims: [], citation_check: { verification: "marker_only", valid_markers: 0, removed_markers: [], uncited_claims: 0 } }}
          sources={toViews(view.sources).map((s) => ({ ...s, text: "" }))}
        />
      );
    case "done": {
      const d = view.data;
      const secs = `${((d.timings_ms.total || 0) / 1000).toFixed(1)}s`;
      const model = (d.model_used || "").split("/").pop();
      const status = d.cached ? `${model} · cached` : d.llm_used ? `${model} · ${secs}` : `Quoted directly from sources (AI model unavailable) · ${secs}`;
      return <AnswerView status={status} data={d} sources={toViews(d.sources)} />;
    }
  }
}
