import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, errorMessage, getJson } from "./api/client";
import type { AuthConfig } from "./api/types";

export interface Session {
  access_token: string;
  refresh_token?: string;
  email?: string;
}

// The session lives only in this tab (sessionStorage): closing the tab signs out.
const KEY = "contexta.auth";
const load = (): Session | null => {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
};
const save = (s: Session | null) => {
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the session just won't survive a reload */
  }
};

/** Supabase's email links (confirm sign-up, reset password) come back to the app with the
 * session - or an error - in the URL fragment. Read it once at startup and remove it from the URL. */
export function consumeAuthRedirect(): { session?: Session; recovery?: boolean; error?: string } {
  const hash = window.location.hash.slice(1);
  if (!/(^|&)(access_token|error_description)=/.test(hash)) return {};
  const p = new URLSearchParams(hash);
  history.replaceState(null, "", window.location.pathname + window.location.search);
  const error = p.get("error_description");
  if (error) return { error: `${error}.`.replace(/\.\.$/, ".") };
  const access_token = p.get("access_token")!;
  return {
    session: { access_token, refresh_token: p.get("refresh_token") ?? undefined, email: emailFromToken(access_token) },
    recovery: p.get("type") === "recovery",
  };
}

export function emailFromToken(token: string): string | undefined {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(payload)).email;
  } catch {
    return undefined;
  }
}

interface AuthValue {
  config: AuthConfig | null;
  configError: string | null;
  session: Session | null;
  signInWithPassword(email: string, password: string): Promise<void>;
  signInWithToken(token: string): void;
  /** "signed-in" when the project doesn't require email confirmation, else "check-email". */
  signUp(email: string, password: string): Promise<"signed-in" | "check-email">;
  requestPasswordReset(email: string): Promise<void>;
  /** Set a new password for the signed-in user (after following a reset link). */
  setPassword(password: string): Promise<void>;
  /** True after a password-reset link signed the user in: they must choose a new password. */
  recovery: boolean;
  signOut(message?: string): void;
  /** Message to show on the sign-in screen (e.g. "session expired"). */
  notice: string | null;
  /** fetch with the user's token; refreshes once on 401, signs out if that fails. */
  authFetch<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [redirect] = useState(consumeAuthRedirect);
  const [session, setSession] = useState<Session | null>(() => {
    if (redirect.session) save(redirect.session);
    return redirect.session ?? load();
  });
  const [recovery, setRecovery] = useState(!!redirect.recovery);
  const [notice, setNotice] = useState<string | null>(redirect.error ?? null);
  const sessionRef = useRef(session);
  sessionRef.current = session;

  useEffect(() => {
    getJson<AuthConfig>("/auth/config")
      .then(setConfig)
      .catch(() => setConfigError("Could not reach the server."));
  }, []);

  const update = useCallback((s: Session | null) => {
    save(s);
    sessionRef.current = s;
    setSession(s);
  }, []);

  // Supabase Auth (GoTrue) REST calls. Email links return to /workspace (add it to the project's
  // allowed redirect URLs).
  const supabase = useCallback(
    async (path: string, body: object, init: { method?: string; token?: string } = {}) => {
      if (!config?.supabase_url || !config.supabase_anon_key) throw new Error("Email sign-in is not configured.");
      const r = await fetch(`${config.supabase_url}/auth/v1/${path}`, {
        method: init.method ?? "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: config.supabase_anon_key,
          ...(init.token ? { Authorization: `Bearer ${init.token}` } : {}),
        },
        body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error_description || d.msg || d.message || "Something went wrong. Try again.");
      return d;
    },
    [config],
  );
  const returnTo = () => encodeURIComponent(`${window.location.origin}/workspace`);

  const supabaseToken = useCallback(
    async (grant: "password" | "refresh_token", body: object): Promise<Session> => {
      const d = await supabase(`token?grant_type=${grant}`, body);
      return { access_token: d.access_token, refresh_token: d.refresh_token, email: d.user?.email };
    },
    [supabase],
  );

  const signOut = useCallback(
    (message?: string) => {
      update(null);
      setRecovery(false);
      setNotice(message ?? null);
    },
    [update],
  );

  const value = useMemo<AuthValue>(
    () => ({
      config,
      configError,
      session,
      notice,
      signOut,
      async signInWithPassword(email, password) {
        update(await supabaseToken("password", { email, password }));
        setNotice(null);
      },
      signInWithToken(token) {
        update({ access_token: token, email: emailFromToken(token) });
        setNotice(null);
      },
      recovery,
      async signUp(email, password) {
        const d = await supabase(`signup?redirect_to=${returnTo()}`, { email, password });
        setNotice(null);
        if (!d.access_token) return "check-email";
        update({ access_token: d.access_token, refresh_token: d.refresh_token, email: d.user?.email ?? email });
        return "signed-in";
      },
      async requestPasswordReset(email) {
        await supabase(`recover?redirect_to=${returnTo()}`, { email });
      },
      async setPassword(password) {
        const token = sessionRef.current?.access_token;
        if (!token) throw new Error("The reset link has expired. Request a new one.");
        await supabase("user", { password }, { method: "PUT", token });
        setRecovery(false);
      },
      async authFetch<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
        const { json, ...rest } = init;
        const send = (token: string) =>
          fetch(path, {
            ...rest,
            body: json !== undefined ? JSON.stringify(json) : rest.body,
            headers: {
              ...(rest.headers as Record<string, string>),
              ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
              Authorization: `Bearer ${token}`,
            },
          });
        const current = sessionRef.current;
        if (!current) throw new ApiError("Not signed in.", 401);
        let r = await send(current.access_token);
        if (r.status === 401 && current.refresh_token) {
          try {
            const next = { ...current, ...(await supabaseToken("refresh_token", { refresh_token: current.refresh_token })) };
            update(next);
            r = await send(next.access_token);
          } catch {
            /* fall through to sign-out */
          }
        }
        if (r.status === 401) {
          signOut("Your session expired. Sign in again.");
          throw new ApiError("Your session expired.", 401);
        }
        if (!r.ok) throw new ApiError(await errorMessage(r), r.status);
        return (r.status === 204 ? null : await r.json()) as T;
      },
    }),
    [config, configError, session, notice, recovery, signOut, supabase, supabaseToken, update],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
