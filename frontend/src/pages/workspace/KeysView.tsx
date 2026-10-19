import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { ApiKey } from "../../api/types";
import { useAuth } from "../../auth";
import { useWorkspace, when } from "./WorkspaceLayout";

export function KeysView() {
  const { base } = useWorkspace();
  const { authFetch } = useAuth();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<{ name: string; key: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setKeys(await authFetch<ApiKey[]>(`${base}/api-keys`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [authFetch, base]);
  useEffect(() => void load(), [load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const r = await authFetch<{ name: string; api_key: string }>(`${base}/api-keys`, { method: "POST", json: { name: name.trim() } });
      setCreated({ name: r.name, key: r.api_key });
      setCopied(false);
      setName("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const revoke = async (k: ApiKey) => {
    if (!confirm(`Revoke ${k.name}? Anything using it stops working immediately.`)) return;
    try {
      await authFetch(`${base}/api-keys/${k.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="inner">
      <div className="page-head">
        <h1>API keys</h1>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Keys let scripts call this workspace's API with member access. They can ask and upload, but cannot save conversations.
      </p>
      <form className="inline-form" onSubmit={create} aria-label="Create an API key">
        <input type="text" placeholder="Key name, e.g. nightly-sync" aria-label="Key name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn">Create key</button>
      </form>
      {created && (
        <div className="callout" role="status">
          <strong>{created.name}</strong>: copy this key now. It will not be shown again.
          <code>{created.key}</code>
          <button
            className="ghost"
            onClick={() => navigator.clipboard?.writeText(created.key).then(() => setCopied(true), () => {})}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <button className="ghost" onClick={() => setCreated(null)}>
            Done
          </button>
        </div>
      )}
      {error && <p className="err">{error}</p>}
      {keys && keys.length > 0 && (
        <table className="list">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th className="hide-sm">Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id}>
                <td>{k.name}</td>
                <td>
                  <span className={`pill ${k.revoked_at ? "revoked" : "active"}`}>{k.revoked_at ? "revoked" : "active"}</span>
                </td>
                <td className="hide-sm muted">{k.last_used_at ? when(k.last_used_at) : "never"}</td>
                <td className="actions">
                  {!k.revoked_at && (
                    <button className="ghost danger" onClick={() => revoke(k)}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {keys && !keys.length && <p className="empty">No API keys yet.</p>}
    </div>
  );
}
