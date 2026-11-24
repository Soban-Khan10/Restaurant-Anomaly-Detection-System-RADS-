import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import type { Severity } from "../lib/types";

export interface Toast {
  id: number;
  title: string;
  body?: string;
  severity?: Severity;
  href?: string;
}

interface ToastValue {
  push: (t: Omit<Toast, "id">) => void;
}

const ToastContext = createContext<ToastValue | null>(null);
const SHOW_MS = 10_000;
const MAX_VISIBLE = 4;

const accent: Record<Severity, string> = {
  High: "border-l-red-500",
  Medium: "border-l-amber-500",
  Low: "border-l-slate-400",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const navigate = useNavigate();

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);

  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = nextId.current++;
      setToasts((ts) => [{ ...t, id }, ...ts].slice(0, MAX_VISIBLE));
      window.setTimeout(() => dismiss(id), SHOW_MS);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        role="status"
        className="pointer-events-none fixed right-4 top-20 z-50 flex w-[22rem] max-w-[calc(100vw-2rem)] flex-col gap-3"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`toast-in pointer-events-auto rounded-lg border border-l-4 border-slate-200 bg-white p-4 shadow-lg ${
              accent[t.severity ?? "Low"]
            }`}
          >
            <div className="flex items-start gap-3">
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => {
                  if (t.href) navigate(t.href);
                  dismiss(t.id);
                }}
              >
                <p className="text-sm font-semibold text-slate-900">{t.title}</p>
                {t.body && <p className="mt-1 text-sm text-slate-600">{t.body}</p>}
                {t.href && <p className="mt-2 text-xs font-medium text-brand-700">View incident →</p>}
              </button>
              <button
                type="button"
                aria-label="Dismiss"
                className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                onClick={() => dismiss(t.id)}
              >
                <svg viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor" aria-hidden="true">
                  <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4Z" />
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToasts(): ToastValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToasts must be used inside <ToastProvider>");
  return ctx;
}
