import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { AnswerFields, ConversationDetail, Turn, WorkspaceAskResponse, WorkspaceSource } from "../../api/types";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth";
import { AnswerView, Skeleton, type SourceView } from "../../components/Answer";
import { useWorkspace } from "./WorkspaceLayout";

interface ThreadTurn {
  question: string;
  answer?: AnswerFields & { sources: WorkspaceSource[] };
  error?: string;
}

export const workspaceSources = (sources: WorkspaceSource[]): SourceView[] =>
  sources.map((s) => ({
    title: s.filename,
    meta: [s.page_number ? `Page ${s.page_number}` : "", s.section_heading ?? ""].filter(Boolean).join(" · "),
    text: s.text ?? s.snippet ?? "",
  }));

const outcomeNote = (a: AnswerFields) =>
  a.outcome === "extractive_fallback"
    ? "Quoted directly from documents (AI model unavailable)"
    : a.outcome === "insufficient_evidence"
      ? "Not found in this workspace's documents"
      : undefined;

export function AskView() {
  const { convId } = useParams();
  const { ws, base, reloadConversations, retentionDays, refreshBilling } = useWorkspace();
  const { authFetch } = useAuth();
  const navigate = useNavigate();
  const [turns, setTurns] = useState<ThreadTurn[]>([]);
  const [detail, setDetail] = useState<Pick<ConversationDetail, "title" | "expires_at"> | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [save, setSave] = useState(true);
  const [busy, setBusy] = useState(false);
  const [looseHistory, setLooseHistory] = useState<Turn[]>([]); // unsaved follow-ups, this tab only
  const justCreated = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (convId && justCreated.current === convId) {
      justCreated.current = null; // we already show the thread we just started
      return;
    }
    setTurns([]);
    setDetail(null);
    setLoadError(null);
    setLooseHistory([]);
    if (!convId) return;
    let live = true;
    setLoading(true);
    authFetch<ConversationDetail>(`${base}/conversations/${convId}`)
      .then((d) => {
        if (!live) return;
        setDetail({ title: d.title, expires_at: d.expires_at });
        setTurns(d.turns.map((t) => ({ question: t.question, answer: t })));
      })
      .catch((e) => live && setLoadError((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [convId, base, authFetch]);

  useEffect(() => endRef.current?.scrollIntoView?.({ block: "nearest" }), [turns.length]);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const q = question.trim();
    if (q.length < 3 || busy) return;
    setBusy(true);
    setQuestion("");
    const index = turns.length;
    setTurns((t) => [...t, { question: q }]);
    const body: Record<string, unknown> = { question: q, top_k: 5 };
    if (convId) body.conversation_id = convId;
    else if (save) body.save = true;
    else body.history = looseHistory;
    try {
      const d = await authFetch<WorkspaceAskResponse>(`${base}/ask`, { method: "POST", json: body });
      setTurns((t) => t.map((turn, i) => (i === index ? { question: q, answer: d } : turn)));
      if (d.conversation_id && !convId) {
        justCreated.current = d.conversation_id;
        setDetail({ title: q, expires_at: new Date(Date.now() + retentionDays * 86400000).toISOString() });
        navigate(d.conversation_id, { replace: false });
      } else if (convId) {
        setDetail((det) => det && { ...det, expires_at: new Date(Date.now() + retentionDays * 86400000).toISOString() });
      } else if (d.outcome !== "insufficient_evidence") {
        setLooseHistory((h) => [...h, { question: q, answer: d.answer.slice(0, 4000) }].slice(-3));
      }
      if (d.conversation_id) void reloadConversations();
    } catch (err) {
      setTurns((t) => t.map((turn, i) => (i === index ? { question: q, error: (err as Error).message } : turn)));
      setQuestion(q);
      if ((err as ApiError).status === 402) void refreshBilling(); // trial just ran out: show the banner
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!convId || !confirm("Delete this conversation? This cannot be undone.")) return;
    try {
      await authFetch(`${base}/conversations/${convId}`, { method: "DELETE" });
      await reloadConversations();
      navigate(`/workspace/${ws.workspace_id}/ask`);
    } catch (err) {
      alert((err as Error).message);
    }
  };

  const title = detail?.title ?? (convId ? "" : "New conversation");
  return (
    <div className="inner">
      <div className="page-head">
        <h1>{title}</h1>
        {convId && (
          <button className="ghost danger" onClick={remove}>
            Delete
          </button>
        )}
      </div>
      {detail && (
        <p className="small muted" style={{ marginTop: 0 }}>
          Deleted on {new Date(detail.expires_at).toLocaleDateString()} unless you continue it.
        </p>
      )}
      <div className="thread" aria-live="polite">
        {loading && <Skeleton />}
        {loadError && <p className="err">{loadError}</p>}
        {!loading && !loadError && !turns.length && <p className="empty">Ask a question about the documents in {ws.name}.</p>}
        {turns.map((t, i) => (
          <article className="turn" key={i}>
            <p className="q">{t.question}</p>
            {t.error ? (
              <p className="err">{t.error}</p>
            ) : t.answer ? (
              <AnswerView data={t.answer} sources={workspaceSources(t.answer.sources)} status={outcomeNote(t.answer)} collapsibleSources />
            ) : (
              <Skeleton />
            )}
          </article>
        ))}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={submit}>
        <textarea
          rows={2}
          maxLength={500}
          value={question}
          aria-label="Your question"
          placeholder={turns.length ? "Ask a follow-up…" : "Ask about your documents…"}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <div className="row">
          {!convId && (
            <label>
              <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> Save to my history
            </label>
          )}
          <button className="btn" disabled={busy}>
            {busy ? "Searching…" : "Ask"}
          </button>
        </div>
      </form>
    </div>
  );
}
