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
  const [session, setSession] = useState<Session | null>(load);
  const [notice, setNotice] = useState<string | null>(null);
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

  const supabaseToken = useCallback(
    async (grant: "password" | "refresh_token", body: object): Promise<Session> => {
      if (!config?.supabase_url || !config.supabase_anon_key) throw new Error("Email sign-in is not configured.");
      const r = await fetch(`${config.supabase_url}/auth/v1/token?grant_type=${grant}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", apikey: config.supabase_anon_key },
        body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error_description || d.msg || "Sign-in failed.");
      return { access_token: d.access_token, refresh_token: d.refresh_token, email: d.user?.email };
    },
    [config],
  );

  const signOut = useCallback(
    (message?: string) => {
      update(null);
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
    [config, configError, session, notice, signOut, supabaseToken, update],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
