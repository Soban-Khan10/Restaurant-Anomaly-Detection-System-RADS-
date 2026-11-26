// Which cameras are paused right now (GET /api/monitoring/status), shared by the banner, the Overview tiles, live
// view and the pauses page. Refreshed when the server announces a change (monitoring.changed), at the exact time
// a pause starts or ends, and every minute as a fallback.
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { nextChange } from "../lib/pauseForm";
import type { CameraMonitoring } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useLive } from "./LiveContext";

interface MonitoringValue {
  status: CameraMonitoring[];
  loaded: boolean;
  byCamera: (cameraId: number) => CameraMonitoring | undefined;
  reload: () => void;
}

const MonitoringContext = createContext<MonitoringValue | null>(null);
const FALLBACK_MS = 60_000;

export function MonitoringProvider({ children }: { children: ReactNode }) {
  const { subscribe } = useLive();
  const res = useApi<CameraMonitoring[]>("/api/monitoring/status");
  const { reload, data } = res;

  useEffect(() => subscribe((msg) => {
    if (msg.type === "monitoring.changed") reload();
  }), [subscribe, reload]);

  useEffect(() => {
    const id = window.setInterval(reload, FALLBACK_MS);
    return () => window.clearInterval(id);
  }, [reload]);

  // A timed pause ends or a planned one starts: refresh right then (the server also announces it each minute).
  useEffect(() => {
    const at = data ? nextChange(data) : null;
    if (!at) return;
    const delay = at.getTime() - Date.now() + 1500;
    if (delay > 3_600_000) return; // far away: the minute refresh will get there
    const id = window.setTimeout(reload, Math.max(delay, 0));
    return () => window.clearTimeout(id);
  }, [data, reload]);

  const status = useMemo(() => data ?? [], [data]);
  const byCamera = useCallback((id: number) => status.find((s) => s.camera_id === id), [status]);
  const value = useMemo(() => ({ status, loaded: data !== null, byCamera, reload }), [status, data, byCamera, reload]);
  return <MonitoringContext.Provider value={value}>{children}</MonitoringContext.Provider>;
}

export function useMonitoring(): MonitoringValue {
  const ctx = useContext(MonitoringContext);
  if (!ctx) throw new Error("useMonitoring must be used inside <MonitoringProvider>");
  return ctx;
}
