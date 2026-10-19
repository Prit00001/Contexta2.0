import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { Member, Role } from "../../api/types";
import { useAuth } from "../../auth";
import { useWorkspace, when } from "./WorkspaceLayout";

const ROLES: Role[] = ["viewer", "member", "admin"];
const ROLE_HELP: Record<Role, string> = {
  viewer: "can ask questions",
  member: "can also add and delete documents",
  admin: "can also manage members and API keys",
};

export function MembersView() {
  const { ws, base } = useWorkspace();
  const { authFetch, session } = useAuth();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const me = session?.email?.toLowerCase();

  const load = useCallback(async () => {
    try {
      setMembers(await authFetch<Member[]>(`${base}/members`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [authFetch, base]);
  useEffect(() => void load(), [load]);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authFetch(`${base}/members`, { method: "POST", json: { email: email.trim(), role } });
      setEmail("");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const changeRole = async (m: Member, next: Role) => {
    if (!m.email) return;
    setError(null);
    try {
      await authFetch(`${base}/members`, { method: "POST", json: { email: m.email, role: next } });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const remove = async (m: Member) => {
    if (!confirm(`Remove ${m.email ?? m.user_id} from ${ws.name}?`)) return;
    try {
      await authFetch(`${base}/members/${m.user_id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="inner">
      <div className="page-head">
        <h1>Members</h1>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        {ROLES.map((r) => `${r[0].toUpperCase()}${r.slice(1)}s ${ROLE_HELP[r]}`).join(". ")}.
      </p>
      <form className="inline-form" onSubmit={add} aria-label="Add a member">
        <input type="email" placeholder="Email of a signed-up user" aria-label="Email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <button className="btn" disabled={busy}>
          Add
        </button>
      </form>
      {error && <p className="err">{error}</p>}
      {members && (
        <table className="list">
          <thead>
            <tr>
              <th>Member</th>
              <th>Role</th>
              <th className="hide-sm">Since</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {members.map((m) => {
              const isMe = !!me && m.email?.toLowerCase() === me;
              return (
                <tr key={m.user_id}>
                  <td>
                    {m.email ?? <span className="muted">{m.user_id}</span>}
                    {isMe && <span className="muted"> (you)</span>}
                  </td>
                  <td>
                    {isMe || !m.email ? (
                      m.role
                    ) : (
                      <select aria-label={`Role of ${m.email}`} value={m.role} onChange={(e) => changeRole(m, e.target.value as Role)}>
                        {ROLES.map((r) => (
                          <option key={r}>{r}</option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td className="hide-sm muted">{when(m.created_at)}</td>
                  <td className="actions">
                    {!isMe && (
                      <button className="ghost danger" onClick={() => remove(m)}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
