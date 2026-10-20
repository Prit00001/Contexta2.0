import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentRow } from "../../api/types";
import { ApiError } from "../../api/client";
import { useAuth } from "../../auth";
import { ROLE_RANK, useWorkspace, when } from "./WorkspaceLayout";

const PENDING = new Set(["queued", "processing"]);

export function DocumentsView() {
  const { ws, base, refreshBilling } = useWorkspace();
  const { authFetch } = useAuth();
  const [docs, setDocs] = useState<DocumentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const canEdit = ROLE_RANK[ws.role] >= ROLE_RANK.member;

  const load = useCallback(async () => {
    try {
      setDocs(await authFetch<DocumentRow[]>(`${base}/documents`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [authFetch, base]);

  useEffect(() => {
    setDocs(null);
    void load();
  }, [load]);

  // Refresh while anything is still being ingested by the worker.
  const pending = docs?.some((d) => PENDING.has(d.status));
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [pending, load]);

  const upload = async (files: File[]) => {
    setError(null);
    setUploading((n) => n + files.length);
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        await authFetch(`${base}/documents`, { method: "POST", body: fd });
      } catch (e) {
        setError(`${f.name}: ${(e as Error).message}`);
        if ((e as ApiError).status === 402) {
          void refreshBilling();
          break; // the rest would be refused too
        }
      } finally {
        setUploading((n) => n - 1);
      }
    }
    await load();
  };

  const remove = async (d: DocumentRow) => {
    if (!confirm(`Delete ${d.filename}? Its passages are removed from search.`)) return;
    try {
      await authFetch(`${base}/documents/${d.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="inner">
      <div className="page-head">
        <h1>Documents</h1>
      </div>
      {canEdit ? (
        <div
          className={`drop${drag ? " drag" : ""}`}
          onDragEnter={(e) => (e.preventDefault(), setDrag(true))}
          onDragOver={(e) => (e.preventDefault(), setDrag(true))}
          onDragLeave={(e) => (e.preventDefault(), setDrag(false))}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            void upload([...e.dataTransfer.files]);
          }}
        >
          {uploading > 0 ? (
            <p style={{ margin: 0 }}>Uploading {uploading} file{uploading === 1 ? "" : "s"}…</p>
          ) : (
            <p style={{ margin: 0 }}>
              Drop PDF, DOCX, TXT or MD files here, or{" "}
              <button type="button" className="linkbtn" onClick={() => fileRef.current?.click()}>
                choose files
              </button>
            </p>
          )}
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md"
            hidden
            data-testid="file-input"
            onChange={(e) => {
              void upload([...(e.target.files ?? [])]);
              e.target.value = "";
            }}
          />
        </div>
      ) : (
        <p className="small muted">You have view access: you can ask questions but not add or delete documents.</p>
      )}
      {error && <p className="err">{error}</p>}
      {docs && docs.length > 0 && (
        <table className="list">
          <thead>
            <tr>
              <th>File</th>
              <th>Status</th>
              <th className="hide-sm">Added</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id}>
                <td>
                  {d.filename}
                  {d.error && (
                    <div className="small" style={{ color: "var(--err)" }}>
                      {d.error}
                    </div>
                  )}
                </td>
                <td>
                  <span className={`pill ${d.status}`}>{d.status}</span>
                </td>
                <td className="hide-sm muted">{when(d.created_at)}</td>
                <td className="actions">
                  {canEdit && (
                    <button className="ghost danger" onClick={() => remove(d)}>
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {docs && !docs.length && <p className="empty">No documents yet.</p>}
    </div>
  );
}
