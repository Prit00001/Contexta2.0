import { useState, type FormEvent } from "react";
import { useAuth } from "../../auth";

type Mode = "signin" | "signup" | "forgot";

const MIN_PASSWORD = 8;

export function SignIn() {
  const { config, notice, recovery, signInWithPassword, signInWithToken, signUp, requestPasswordReset } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
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

  if (recovery) return <SetPassword />;

  const withPassword = !!config.supabase_anon_key;
  const go = (m: Mode) => {
    setMode(m);
    setError(null);
    setSent(null);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!withPassword) {
      const t = token.trim();
      if (t.split(".").length !== 3) return setError("That does not look like an access token.");
      return signInWithToken(t);
    }
    if (mode === "signup" && password.length < MIN_PASSWORD) {
      return setError(`Use at least ${MIN_PASSWORD} characters for your password.`);
    }
    setBusy(true);
    try {
      if (mode === "signin") await signInWithPassword(email, password);
      else if (mode === "forgot") {
        await requestPasswordReset(email);
        setSent(`If ${email} has an account, a link to reset its password is on its way.`);
      } else if ((await signUp(email, password)) === "check-email") {
        setSent(`We sent a confirmation link to ${email}. Open it to finish creating your account.`);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!withPassword) {
    return (
      <div className="center">
        <h1>Sign in</h1>
        {notice && <p className="err">{notice}</p>}
        <form className="stack" onSubmit={submit}>
          <p className="small muted" style={{ margin: 0 }}>
            Email sign-in is not configured on this server (set <code>SUPABASE_URL</code> and <code>SUPABASE_ANON_KEY</code>). Paste a
            Supabase access token instead:
          </p>
          <textarea rows={4} placeholder="eyJhbGciOi…" aria-label="Access token" required value={token} onChange={(e) => setToken(e.target.value)} />
          <button className="btn">Continue</button>
        </form>
        {error && <p className="err">{error}</p>}
      </div>
    );
  }

  const heading = { signin: "Sign in", signup: "Create your account", forgot: "Reset your password" }[mode];
  const action = { signin: "Sign in", signup: "Create account", forgot: "Send reset link" }[mode];
  return (
    <div className="center">
      <h1>{heading}</h1>
      <p className="muted" style={{ margin: 0 }}>
        {mode === "signup"
          ? "Your first workspace is free for an hour of active use."
          : mode === "forgot"
            ? "We'll email you a link to choose a new password."
            : "Ask questions about your team's own documents."}
      </p>
      {notice && <p className="err">{notice}</p>}
      {sent ? (
        <p className="callout" role="status">
          {sent}
        </p>
      ) : (
        <form className="stack" onSubmit={submit}>
          <input type="email" placeholder="Email" aria-label="Email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          {mode !== "forgot" && (
            <input
              type="password"
              placeholder={mode === "signup" ? `Password (${MIN_PASSWORD}+ characters)` : "Password"}
              aria-label="Password"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
          <button className="btn" disabled={busy}>
            {action}
          </button>
        </form>
      )}
      {error && <p className="err">{error}</p>}
      <p className="small muted switch">
        {mode === "signin" ? (
          <>
            New here?{" "}
            <button type="button" className="linkbtn" onClick={() => go("signup")}>
              Create an account
            </button>
            {" · "}
            <button type="button" className="linkbtn" onClick={() => go("forgot")}>
              Forgot password?
            </button>
          </>
        ) : (
          <>
            {mode === "signup" ? "Already have an account? " : ""}
            <button type="button" className="linkbtn" onClick={() => go("signin")}>
              Back to sign in
            </button>
          </>
        )}
      </p>
    </div>
  );
}

/** Shown after a password-reset link signed the user in. */
function SetPassword() {
  const { setPassword, signOut } = useAuth();
  const [password, setPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < MIN_PASSWORD) return setError(`Use at least ${MIN_PASSWORD} characters.`);
    setBusy(true);
    setError(null);
    try {
      await setPassword(password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="center">
      <h1>Choose a new password</h1>
      <form className="stack" onSubmit={submit}>
        <input
          type="password"
          placeholder={`New password (${MIN_PASSWORD}+ characters)`}
          aria-label="New password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPw(e.target.value)}
        />
        <button className="btn" disabled={busy}>
          Save password
        </button>
      </form>
      {error && <p className="err">{error}</p>}
      <p className="small muted switch">
        <button type="button" className="linkbtn" onClick={() => signOut()}>
          Cancel
        </button>
      </p>
    </div>
  );
}
