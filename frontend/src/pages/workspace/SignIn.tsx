import { useState, type FormEvent } from "react";
import { useAuth } from "../../auth";

export function SignIn() {
  const { config, notice, signInWithPassword, signInWithToken } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!config) return null;

  if (!config.workspaces_enabled) {
    return (
      <div className="center">
        <h1>Workspaces</h1>
        <p className="muted">Workspaces are not enabled on this server (no database configured).</p>
      </div>
    );
  }

  const withPassword = !!config.supabase_anon_key;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!withPassword) {
      const t = token.trim();
      if (t.split(".").length !== 3) return setError("That does not look like an access token.");
      return signInWithToken(t);
    }
    setBusy(true);
    try {
      await signInWithPassword(email, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="center">
      <h1>Sign in</h1>
      <p className="muted" style={{ margin: 0 }}>
        Ask questions about your team's own documents.
      </p>
      {notice && <p className="err">{notice}</p>}
      <form className="stack" onSubmit={submit}>
        {withPassword ? (
          <>
            <input type="email" placeholder="Email" aria-label="Email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <input type="password" placeholder="Password" aria-label="Password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </>
        ) : (
          <>
            <p className="small muted" style={{ margin: 0 }}>
              Email sign-in is not configured on this server (set <code>SUPABASE_URL</code> and <code>SUPABASE_ANON_KEY</code>). Paste a
              Supabase access token instead:
            </p>
            <textarea rows={4} placeholder="eyJhbGciOi…" aria-label="Access token" required value={token} onChange={(e) => setToken(e.target.value)} />
          </>
        )}
        <button className="btn" disabled={busy}>
          {withPassword ? "Sign in" : "Continue"}
        </button>
      </form>
      {error && <p className="err">{error}</p>}
    </div>
  );
}
