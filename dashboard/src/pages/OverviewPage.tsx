import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useAuth } from "../auth/AuthContext";
import { CameraIcon } from "../components/Icons";
import { Card, CameraStatusBadge, EmptyState, ErrorState, SeverityBadge, Spinner, StatusBadge } from "../components/ui";
import { cameraStreamUrl } from "../lib/api";
import { formatTime, timeAgo } from "../lib/format";
import type { Alert, Camera, IncidentSummary } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useIncidentEvents, useLive } from "../live/LiveContext";

const REFRESH_MS = 60_000; // escalations are not sent over the WebSocket, so look again every minute

function useEvery(ms: number, fn: () => void) {
  useEffect(() => {
    const id = window.setInterval(fn, ms);
    return () => window.clearInterval(id);
  }, [ms, fn]);
}

// ---------------------------------------------------------------- today's numbers

function StatCard({ label, value, hint, to, tone = "default" }:
  { label: string; value: number | string; hint?: string; to?: string; tone?: "default" | "attention" }) {
  const body = (
    <div className={`h-full rounded-xl border bg-white p-5 shadow-sm transition ${
      to ? "hover:border-brand-300 hover:shadow" : ""} ${tone === "attention" ? "border-amber-200" : "border-slate-200"}`}>
      <p className="text-sm text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-semibold tabular-nums ${tone === "attention" ? "text-amber-700" : "text-slate-900"}`}>
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
  return to ? <Link to={to} className="block">{body}</Link> : body;
}

function TodayStats({ summary }: { summary: IncidentSummary | null }) {
  const s = summary;
  const today = s ? `date_from=${s.date_from}&date_to=${s.date_to}` : "";
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatCard label="Incidents today" value={s?.total ?? "–"} to={`/incidents?${today}`} />
      <StatCard label="Not reviewed yet" value={s?.unreviewed ?? "–"} hint="Waiting for someone to check them"
        to={`/incidents?${today}&status=New`} tone={s && s.unreviewed > 0 ? "attention" : "default"} />
      <StatCard label="Confirmed" value={s?.by_status["Confirmed violation"] ?? "–"}
        to={`/incidents?${today}&status=${encodeURIComponent("Confirmed violation")}`} />
      <StatCard label="False alarms" value={s?.by_status["False alarm"] ?? "–"}
        to={`/incidents?${today}&status=${encodeURIComponent("False alarm")}`} />
    </div>
  );
}

// ---------------------------------------------------------------- cameras

function CameraTile({ camera }: { camera: Camera }) {
  const { token } = useAuth();
  const { cameras: live } = useLive();
  const [showLive, setShowLive] = useState(false);
  const [streamFailed, setStreamFailed] = useState(false);
  const beat = live[camera.camera_id];
  const status = beat?.status ?? camera.status;
  const fps = beat ? beat.fps : camera.fps;

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="relative aspect-video bg-slate-900">
        {showLive && token && !streamFailed ? (
          <img src={cameraStreamUrl(camera.camera_id, token)} alt={`Live view of ${camera.name}`}
            className="h-full w-full object-contain" onError={() => setStreamFailed(true)} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-slate-400">
            <CameraIcon className="h-8 w-8" />
            <p className="text-xs">
              {streamFailed ? "Live view is not available right now (the camera service is not running)."
                : "Live view is hidden"}
            </p>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900">{camera.name}</p>
          <p className="text-xs text-slate-500">
            {!camera.enabled ? "Turned off in settings"
              : status === "online" && fps != null ? `${fps.toFixed(1)} frames per second`
              : status === "online" ? "Receiving pictures"
              : "No signal from this camera"}
            {beat && status !== "offline" ? ` · ${formatTime(beat.ts)}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <CameraStatusBadge status={camera.enabled ? status : "offline"} />
          <button type="button" className="btn-secondary !px-2.5 !py-1 text-xs"
            onClick={() => { setStreamFailed(false); setShowLive((v) => !v); }}>
            {showLive ? "Hide" : "Live view"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- chart

function HazardChart({ summary }: { summary: IncidentSummary }) {
  const data = summary.by_hazard.map((h) => ({ name: h.name, count: h.count }));
  return (
    <div className="px-2 pb-4 pt-2">
      {summary.total === 0 && (
        <p className="px-3 pb-1 text-sm text-slate-500">No incidents so far today.</p>
      )}
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 36, bottom: 4, left: 8 }}>
          <CartesianGrid horizontal={false} stroke="#e2e8f0" />
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12, fill: "#64748b" }} axisLine={false}
            tickLine={false} />
          <YAxis type="category" dataKey="name" width={180} tick={{ fontSize: 12, fill: "#334155" }} axisLine={false}
            tickLine={false} />
          <Tooltip cursor={{ fill: "#f1f5f9" }}
            formatter={(v) => [`${v} incident${v === 1 ? "" : "s"}`, "Today"]}
            contentStyle={{ borderRadius: 8, borderColor: "#e2e8f0", fontSize: 13 }} />
          <Bar dataKey="count" fill="#1f5f8b" radius={[0, 4, 4, 0]} maxBarSize={28} isAnimationActive={false}>
            <LabelList dataKey="count" position="right" style={{ fontSize: 12, fill: "#334155" }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------- alerts

function AlertList({ alerts }: { alerts: Alert[] }) {
  if (alerts.length === 0) return <EmptyState title="No alerts yet" hint="New incidents will appear here." />;
  return (
    <ul className="divide-y divide-slate-100">
      {alerts.map((a) => (
        <li key={a.alert_id}>
          <Link to={`/incidents/${a.incident_id}`} className="flex items-start gap-3 px-5 py-3.5 hover:bg-slate-50">
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900">
                {a.hazard_name}
                {a.kind === "escalation" && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                    Not reviewed for 10 min
                  </span>
                )}
              </p>
              <p className="mt-0.5 truncate text-xs text-slate-500">
                {a.camera_name} · {a.zone_name} · {formatTime(a.start_time)} ({timeAgo(a.sent_at)})
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1.5">
              <SeverityBadge severity={a.severity} />
              <StatusBadge status={a.incident_status} />
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- page

export default function OverviewPage() {
  const cameras = useApi<Camera[]>("/api/cameras");
  const summary = useApi<IncidentSummary>("/api/incidents/summary"); // server's "today" (Pakistan time)
  const alerts = useApi<Alert[]>("/api/alerts", { limit: 8 });
  const { reload: reloadSummary } = summary;
  const { reload: reloadAlerts } = alerts;

  const refresh = useCallback(() => {
    reloadSummary();
    reloadAlerts();
  }, [reloadSummary, reloadAlerts]);
  useIncidentEvents(refresh);
  useEvery(REFRESH_MS, refresh);

  return (
    <div className="space-y-6">
      <TodayStats summary={summary.data} />

      <section aria-labelledby="cameras-heading">
        <h2 id="cameras-heading" className="mb-3 text-sm font-semibold text-slate-800">Cameras</h2>
        {cameras.error ? <Card><ErrorState message={cameras.error} onRetry={cameras.reload} /></Card>
          : !cameras.data ? <Card><Spinner /></Card>
          : cameras.data.length === 0 ? <Card><EmptyState title="No cameras set up yet" /></Card>
          : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {cameras.data.map((c) => <CameraTile key={c.camera_id} camera={c} />)}
            </div>
          )}
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Today's incidents by hazard" className="lg:col-span-3">
          {summary.error ? <ErrorState message={summary.error} onRetry={summary.reload} />
            : summary.data ? <HazardChart summary={summary.data} /> : <Spinner />}
        </Card>
        <Card title="Latest alerts" className="lg:col-span-2"
          action={<Link to="/incidents" className="text-sm font-medium text-brand-700 hover:underline">All incidents</Link>}>
          {alerts.error ? <ErrorState message={alerts.error} onRetry={alerts.reload} />
            : alerts.data ? <AlertList alerts={alerts.data} /> : <Spinner />}
        </Card>
      </div>
    </div>
  );
}
