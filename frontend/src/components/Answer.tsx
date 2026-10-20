import { useId, useRef, useState, type ReactNode } from "react";
import type { AnswerFields, Claim } from "../api/types";

/** A source as the answer view shows it, whichever endpoint it came from. */
export interface SourceView {
  title: string;
  url?: string;
  meta: string;
  text: string;
}

interface Props {
  data: AnswerFields;
  sources: SourceView[];
  /** Line above the answer (model, timing, cached...). */
  status?: string;
  /** Sources start collapsed behind a "N sources" toggle (workspace threads). */
  collapsibleSources?: boolean;
}

const reduceMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function AnswerView({ data, sources, status, collapsibleSources = false }: Props) {
  const prefix = useId();
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [listOpen, setListOpen] = useState(!collapsibleSources);
  const [flash, setFlash] = useState<number | null>(null);
  const listRef = useRef<HTMLOListElement>(null);

  const showSource = (n: number) => {
    if (!sources[n - 1]) return;
    setListOpen(true);
    setOpen((s) => new Set(s).add(n));
    setFlash(n);
    setTimeout(() => setFlash(null), 1200);
    requestAnimationFrame(() =>
      document
        .getElementById(`${prefix}-src-${n}`)
        ?.scrollIntoView?.({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" }),
    );
  };

  const cite = (n: number, key: string) => {
    const s = sources[n - 1];
    const label = s ? `Source ${n}: ${s.title}` : `Source ${n}`;
    return (
      <button key={key} type="button" className="cite" title={label} aria-label={label} onClick={() => showSource(n)}>
        {n}
      </button>
    );
  };

  return (
    <div className="answer-block">
      {status && <p className="status">{status}</p>}
      <div className="answer">
        {data.claims.length ? <Claims claims={data.claims} cite={cite} /> : <PlainAnswer text={data.answer} cite={cite} />}
      </div>
      <Evidence data={data} />
      {data.standalone_question && <p className="evidence">Read as: “{data.standalone_question}”</p>}
      {!!data.query_rewrites?.length && (
        <p className="evidence">Also searched for: {data.query_rewrites.map((q) => `“${q}”`).join(", ")}</p>
      )}
      {data.warnings?.map((w) => (
        <p className="note" key={w}>
          {w}
        </p>
      ))}
      {sources.length > 0 && (
        <section className="sources">
          {collapsibleSources ? (
            <button type="button" className="sources-toggle" aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)}>
              {listOpen ? "▾" : "▸"} {sources.length} {sources.length === 1 ? "source" : "sources"}
            </button>
          ) : (
            <h2 className="label">Sources</h2>
          )}
          {listOpen && (
            <ol className="srcs" ref={listRef}>
              {sources.map((s, i) => (
                <li key={i} id={`${prefix}-src-${i + 1}`} className={`src${flash === i + 1 ? " flash" : ""}`}>
                  <span className="n">{i + 1}</span>
                  <div>
                    {s.url ? (
                      <a className="t" href={s.url} target="_blank" rel="noopener noreferrer">
                        {s.title}
                      </a>
                    ) : (
                      <span className="t">{s.title}</span>
                    )}
                    {s.meta && <span className="meta">{s.meta}</span>}
                    {s.text && (
                      <details
                        open={open.has(i + 1)}
                        onToggle={(e) => {
                          const isOpen = (e.target as HTMLDetailsElement).open;
                          setOpen((prev) => {
                            const next = new Set(prev);
                            if (isOpen) next.add(i + 1);
                            else next.delete(i + 1);
                            return next;
                          });
                        }}
                      >
                        <summary>Passage</summary>
                        <p>{s.text}</p>
                      </details>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </div>
  );
}

type Cite = (n: number, key: string) => ReactNode;

/** Sentence by sentence: flags uncited sentences and ones a claim check found unsupported. */
function Claims({ claims, cite }: { claims: Claim[]; cite: Cite }) {
  return (
    <p>
      {claims.map((c, i) => {
        let cls = "claim";
        let why: string | undefined;
        if (c.status === "uncited") {
          cls += " uncited";
          why = "No citation: this sentence is not tied to a retrieved source.";
        } else if (c.support === "not_supported") {
          cls += " unsupported";
          why =
            c.support_method === "numbers" && c.unsupported_numbers?.length
              ? `Not in the cited source: ${c.unsupported_numbers.join(", ")}. Check this figure before relying on it.`
              : "An AI check found the cited source does not back this sentence.";
        }
        return (
          <span key={i}>
            <span className={cls} title={why}>
              {c.text}
            </span>
            {c.markers.length > 0 && " "}
            {c.markers.map((m, j) => cite(m, `${i}-${j}`))}{" "}
          </span>
        );
      })}
    </p>
  );
}

/** Fallback when there are no claims: paragraphs with [n] turned into citation buttons. */
export function PlainAnswer({ text, cite }: { text: string; cite: Cite }) {
  return (
    <>
      {text.split(/\n{2,}/).map((para, i) => (
        <p key={i}>
          {para.split(/(\[\d+\])/).map((part, j) => {
            const m = /^\[(\d+)\]$/.exec(part);
            return m ? cite(Number(m[1]), `${i}-${j}`) : part;
          })}
        </p>
      ))}
    </>
  );
}

function Evidence({ data }: { data: AnswerFields }) {
  const cc = data.citation_check;
  const n = data.claims.length;
  if (!n || data.outcome === "insufficient_evidence") return null;
  const cited = data.claims.filter((c) => c.status === "cited").length;
  const parts: ReactNode[] = [cited === n ? "Every sentence cites a source" : `${cited} of ${n} sentences cite a source`];
  if (cc?.verification === "claim_support") {
    parts.push(
      <>
        AI claim check: {cc.supported_claims || 0} supported
        {!!cc.not_supported_claims && (
          <>
            , <span className="flag">{cc.not_supported_claims} not supported</span>
          </>
        )}
        {!!cc.unknown_claims && <>, {cc.unknown_claims} unchecked</>}
      </>,
    );
  }
  if (cc?.verification === "local_checks" && cc.not_supported_claims) {
    const k = cc.not_supported_claims;
    parts.push(
      <span className="flag">
        {k === 1 ? "1 sentence states a figure" : `${k} sentences state figures`} not found in the cited source
      </span>,
    );
  }
  if (cc?.removed_markers.length) {
    const what =
      cc.removed_markers.length === 1
        ? "a citation to a source that was"
        : `${cc.removed_markers.length} citations to sources that were`;
    parts.push(
      <span className="flag">
        removed {what} never retrieved ({cc.removed_markers.map((m) => `[${m}]`).join(" ")})
      </span>,
    );
  }
  return (
    <p className="evidence">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {p}
        </span>
      ))}
    </p>
  );
}

export function Skeleton() {
  return (
    <div aria-hidden="true">
      <div className="skel" style={{ width: "92%" }} />
      <div className="skel" style={{ width: "84%" }} />
      <div className="skel" style={{ width: "58%" }} />
    </div>
  );
}
