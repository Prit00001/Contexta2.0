import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, NavLink, Navigate, Outlet, useNavigate, useOutletContext, useParams } from "react-router-dom";
import type { Billing, ConversationSummary, Workspace } from "../../api/types";
import { useAuth } from "../../auth";
import { TrialBanner, useBilling } from "./billing";
import { SignIn } from "./SignIn";

export const ROLE_RANK = { viewer: 0, member: 1, admin: 2 } as const;

export interface WorkspaceCtx {
  ws: Workspace;
  base: string; // /workspaces/{id} API prefix
  conversations: ConversationSummary[] | null;
  reloadConversations(): Promise<void>;
  retentionDays: number;
  billing: Billing | null;
  refreshBilling(): Promise<void>;
}

export const useWorkspace = () => useOutletContext<WorkspaceCtx>();

const remember = (id?: string) => {
  try {
    if (id) localStorage.setItem("contexta.ws", id);
    return localStorage.getItem("contexta.ws");
  } catch {
    return null;
  }
};

/** Loads the user's workspaces; null while loading. */
function useWorkspaces() {
  const { authFetch, session } = useAuth();
  const [list, setList] = useState<Workspace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    try {
      setList(await authFetch<Workspace[]>("/workspaces"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [authFetch]);
  useEffect(() => {
    if (session) void reload();
  }, [session, reload]);
  return { list, error, reload };
}

/** /workspace: sign in, then go to the remembered workspace (or create the first one). */
export function WorkspaceHome() {
  const { session, config, configError } = useAuth();
  const { list, error, reload } = useWorkspaces();
  if (configError) return <p className="err center">{configError}</p>;
  if (!config) return null;
  if (!session) return <SignIn />;
  if (error) return <p className="err center">{error}</p>;
  if (!list) return null;
  if (!list.length) return <CreateWorkspace first onCreated={reload} />;
  const pick = list.find((w) => w.workspace_id === remember()) ?? list[0];
  return <Navigate to={`/workspace/${pick.workspace_id}/ask`} replace />;
}

export function WorkspaceLayout() {
  const { wsId } = useParams();
  const { session, config, authFetch } = useAuth();
  const { list, error, reload } = useWorkspaces();
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const ws = list?.find((w) => w.workspace_id === wsId);
  const { billing, refresh: refreshBilling } = useBilling(ws?.workspace_id);

  const reloadConversations = useCallback(async () => {
    if (!wsId) return;
    try {
      setConversations(await authFetch<ConversationSummary[]>(`/workspaces/${wsId}/conversations`));
    } catch {
      setConversations([]);
    }
  }, [authFetch, wsId]);

  useEffect(() => {
    setConversations(null);
    if (ws) {
      remember(ws.workspace_id);
      void reloadConversations();
    }
  }, [ws, reloadConversations]);

  if (!config) return null;
  if (!session) return <SignIn />;
  if (error) return <p className="err center">{error}</p>;
  if (!list) return null;
  if (!ws) return <p className="empty">This workspace doesn't exist or you're not a member. <Link to="/workspace">Your workspaces</Link></p>;

  const isAdmin = ws.role === "admin";
  const tab = ({ isActive }: { isActive: boolean }) => `tab${isActive ? " active" : ""}`;
  const ctx: WorkspaceCtx = {
    ws,
    base: `/workspaces/${ws.workspace_id}`,
    conversations,
    reloadConversations,
    retentionDays: config.history_retention_days,
    billing,
    refreshBilling,
  };

  return (
    <div className="ws">
      <aside>
        <div>
          <div className="side-head">
            <span className="label">Workspace</span>
            <button className="ghost" onClick={() => setCreating(true)}>
              + New
            </button>
          </div>
          <select
            aria-label="Workspace"
            value={ws.workspace_id}
            onChange={(e) => navigate(`/workspace/${e.target.value}/ask`)}
          >
            {list.map((w) => (
              <option key={w.workspace_id} value={w.workspace_id}>
                {w.name} ({w.role})
              </option>
            ))}
          </select>
        </div>
        <nav className="tabs" aria-label="Workspace sections">
          <NavLink to="ask" className={tab}>
            Ask
          </NavLink>
          <NavLink to="documents" className={tab}>
            Documents
          </NavLink>
          {isAdmin && (
            <>
              <NavLink to="members" className={tab}>
                Members
              </NavLink>
              <NavLink to="keys" className={tab}>
                API keys
              </NavLink>
              {billing?.enabled && (
                <NavLink to="billing" className={tab}>
                  Billing
                </NavLink>
              )}
            </>
          )}
        </nav>
        <ConversationList conversations={conversations} />
        <p className="privacy">
          Saved conversations are visible only to you and deleted {config.history_retention_days} days after the last message.
        </p>
      </aside>
      <main>
        <TrialBanner billing={billing} wsId={ws.workspace_id} />
        <Outlet context={ctx} />
      </main>
      {creating && (
        <CreateWorkspace
          onCancel={() => setCreating(false)}
          onCreated={async (id) => {
            setCreating(false);
            await reload();
            navigate(`/workspace/${id}/ask`);
          }}
        />
      )}
    </div>
  );
}

export const when = (iso: string) => {
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days < 1) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return days < 7 ? `${days}d ago` : d.toLocaleDateString();
};

function ConversationList({ conversations }: { conversations: ConversationSummary[] | null }) {
  const { convId } = useParams();
  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
      <div className="side-head">
        <span className="label">Conversations</span>
        <Link className="ghost" to="ask">
          + New
        </Link>
      </div>
      {conversations && !conversations.length && <p className="small muted" style={{ margin: 0 }}>No saved conversations yet.</p>}
      <ul className="convs">
        {conversations?.map((c) => (
          <li key={c.id}>
            <Link to={`ask/${c.id}`} className={`conv${c.id === convId ? " active" : ""}`} aria-current={c.id === convId ? "page" : undefined}>
              <span className="t">{c.title}</span>
              <span className="d">
                {when(c.updated_at)} · {c.turns} {c.turns === 1 ? "question" : "questions"}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CreateWorkspace({ first, onCreated, onCancel }: { first?: boolean; onCreated(id: string): void; onCancel?(): void }) {
  const { authFetch } = useAuth();
  const [org, setOrg] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await authFetch<{ workspace_id: string }>("/workspaces", {
        method: "POST",
        json: { org_name: org.trim(), workspace_name: name.trim() },
      });
      onCreated(r.workspace_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const form = (
    <form onSubmit={submit} aria-label="New workspace">
      <h2 style={first ? { fontSize: 22, margin: "0 0 4px" } : undefined}>{first ? "Create your first workspace" : "New workspace"}</h2>
      {first && <p className="muted" style={{ margin: 0 }}>A workspace holds your team's documents. You'll be its admin.</p>}
      <div className="stack">
        <input type="text" placeholder="Organisation name" aria-label="Organisation name" required maxLength={200} value={org} onChange={(e) => setOrg(e.target.value)} autoFocus />
        <input type="text" placeholder="Workspace name" aria-label="Workspace name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {error && <p className="err">{error}</p>}
      <div className="actions" style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        {onCancel && (
          <button type="button" className="ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button className="btn" disabled={busy}>
          Create
        </button>
      </div>
    </form>
  );
  return first ? <div className="center">{form}</div> : <div className="modal">{form}</div>;
}
