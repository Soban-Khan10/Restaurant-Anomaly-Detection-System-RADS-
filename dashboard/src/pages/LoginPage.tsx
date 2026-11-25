import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { ReviewNote } from "../components/ui";
import { ApiError } from "../lib/api";

interface LocationState {
  from?: { pathname: string; search?: string };
}

export default function LoginPage() {
  const { status, signIn, notice } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const state = (location.state ?? {}) as LocationState;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "signed-in") return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(username.trim(), password);
      const from = state.from ? state.from.pathname + (state.from.search ?? "") : "/";
      navigate(from === "/login" ? "/" : from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Please try again.");
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="grid h-12 w-12 place-items-center rounded-xl bg-brand-600 text-lg font-bold text-white shadow-sm">
            R
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-slate-900">RADS</h1>
          <p className="mt-1 text-sm text-slate-500">Kitchen food-safety monitoring</p>
        </div>

        <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm" noValidate>
          <h2 className="text-base font-semibold text-slate-900">Sign in</h2>
          {notice && !error && (
            <p className="mt-3 rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-800" role="status">{notice}</p>
          )}
          {error && (
            <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</p>
          )}
          <div className="mt-5 space-y-4">
            <div>
              <label htmlFor="username" className="field-label">Username</label>
              <input id="username" className="field" autoComplete="username" autoFocus required value={username}
                onChange={(e) => setUsername(e.target.value)} />
            </div>
            <div>
              <label htmlFor="password" className="field-label">Password</label>
              <input id="password" type="password" className="field" autoComplete="current-password" required
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
          </div>
          <button type="submit" className="btn-primary mt-6 w-full" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="mt-6">
          <ReviewNote compact />
        </div>
      </div>
    </div>
  );
}
