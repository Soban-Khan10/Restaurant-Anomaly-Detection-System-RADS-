import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { ApiError, api, onUnauthorized, tokenExpiry, tokenStore } from "../lib/api";
import type { LoginResponse, Role, User } from "../lib/types";

type Status = "checking" | "signed-out" | "signed-in";

interface AuthValue {
  status: Status;
  user: User | null;
  token: string | null;
  /** Why the user was signed out (shown on the login page), e.g. the session expired. */
  notice: string | null;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: (notice?: string) => void;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export const SESSION_ENDED = "Your session has ended. Please sign in again.";

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(() => tokenStore.get());
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<Status>(token ? "checking" : "signed-out");
  const [notice, setNotice] = useState<string | null>(null);

  const signOut = useCallback(
    (notice?: string) => {
      tokenStore.clear();
      setToken(null);
      setUser(null);
      setStatus("signed-out");
      // Kept in state, not only in the navigation: React Router runs navigations as transitions, so the
      // route guard may redirect to /login first (without this message) and win.
      setNotice(notice ?? null);
      navigate("/login", { replace: true });
    },
    [navigate],
  );

  // Any 401 from the server (expired token, deactivated account) ends the session.
  useEffect(() => {
    onUnauthorized(() => signOut(SESSION_ENDED));
    return () => onUnauthorized(null);
  }, [signOut]);

  // On start-up with a stored token: check it is still valid and load the user.
  useEffect(() => {
    if (!token || user) return;
    const ctrl = new AbortController();
    api<User>("/api/auth/me", { signal: ctrl.signal })
      .then((u) => {
        setUser(u);
        setStatus("signed-in");
      })
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        // A 401 has already signed the user out (onUnauthorized). Anything else, e.g. server not running:
        if (!(e instanceof ApiError && e.status === 401)) signOut(e.message);
      });
    return () => ctrl.abort();
  }, [token, user, signOut]);

  // Sign out automatically when the 8-hour token expires.
  useEffect(() => {
    const exp = token ? tokenExpiry(token) : null;
    if (exp === null) return;
    const ms = exp * 1000 - Date.now();
    if (ms <= 0) {
      signOut(SESSION_ENDED);
      return;
    }
    const id = window.setTimeout(() => signOut(SESSION_ENDED), Math.min(ms, 2_147_000_000));
    return () => window.clearTimeout(id);
  }, [token, signOut]);

  const signIn = useCallback(async (username: string, password: string) => {
    const res = await api<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: { username, password },
      auth: false,
    });
    tokenStore.set(res.access_token);
    setNotice(null);
    setToken(res.access_token);
    setUser(res.user);
    setStatus("signed-in");
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      user,
      token,
      notice,
      signIn,
      signOut,
      hasRole: (...roles: Role[]) => !!user && roles.includes(user.role),
    }),
    [status, user, token, notice, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
