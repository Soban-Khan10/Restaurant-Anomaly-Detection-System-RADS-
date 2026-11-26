import { useEffect, useState } from "react";
import { PauseIcon } from "../components/Icons";
import { EndPauseDialog, PauseDialog } from "../components/PauseDialog";
import { Card, ChoicePill, EmptyState, ErrorState, Spinner } from "../components/ui";
import { formatDateTime, pakistanDate, pakistanDateDaysAgo } from "../lib/format";
import type { Camera, Pause, PauseState } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useLive } from "../live/LiveContext";

const STATES: { value: PauseState; label: string; style: string }[] = [
  { value: "active", label: "Paused now", style: "bg-amber-100 text-amber-900 ring-amber-300" },
  { value: "planned", label: "Planned", style: "bg-brand-50 text-brand-800 ring-brand-200" },
  { value: "ended", label: "Ended", style: "bg-slate-100 text-slate-700 ring-slate-200" },
  { value: "cancelled", label: "Cancelled", style: "bg-white text-slate-500 ring-slate-200" },
];

function minutesText(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  const h = Math.floor(m / 60);
  return h ? `${h} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`;
}

function duration(p: Pause, now: Date): string {
  const start = new Date(p.starts_at).getTime();
  const ends = [p.ends_at, p.ended_at].filter(Boolean).map((t) => new Date(t!).getTime());
  if (p.state === "cancelled") return "—";
  if (p.state === "planned") return ends.length ? minutesText(Math.min(...ends) - start) : "until resumed";
  if (p.state === "active") return `${minutesText(now.getTime() - start)} so far`;
  return minutesText(Math.min(...ends) - start);
}

function toText(p: Pause): string {
  if (p.state === "cancelled") return "—";
  if (p.ended_at && p.state === "ended") return formatDateTime(p.ended_at);
  return p.ends_at ? formatDateTime(p.ends_at) : "Until resumed";
}

export default function PausesPage() {
  const today = pakistanDate();
  const ranges = [
    { key: "today", label: "Today", from: today },
    { key: "7", label: "Last 7 days", from: pakistanDateDaysAgo(6) },
    { key: "30", label: "Last 30 days", from: pakistanDateDaysAgo(29) },
    { key: "all", label: "All time", from: "" },
  ];
  const [range, setRange] = useState("30");
  const [cameraId, setCameraId] = useState("");
  const [state, setState] = useState("");
  const [dialog, setDialog] = useState<{ kind: "new" } | { kind: "end"; pause: Pause } | null>(null);
  const cameras = useApi<Camera[]>("/api/cameras");
  const from = ranges.find((r) => r.key === range)!.from;
  // "to" is left open, so planned pauses after today are listed too.
  const pauses = useApi<Pause[]>("/api/pauses", { from, camera_id: cameraId, state });
  const { subscribe } = useLive();
  const { reload } = pauses;
  const now = new Date();

  useEffect(() => subscribe((msg) => {
    if (msg.type === "monitoring.changed") reload();
  }), [subscribe, reload]);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 p-5">
          <div className="max-w-2xl text-sm text-slate-600">
            <p>
              While monitoring is paused, the camera system detects nothing on that camera: no incidents, no alerts. Live
              view keeps working. Use it for cleaning, closed days or maintenance, so false alarms are not created.
            </p>
          </div>
          <button type="button" className="btn-primary shrink-0" onClick={() => setDialog({ kind: "new" })}
            disabled={!cameras.data}>
            <PauseIcon className="h-4 w-4" /> Pause monitoring
          </button>
        </div>
        <div className="flex flex-wrap items-end gap-4 border-t border-slate-100 px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-medium text-slate-600">Period:</span>
            {ranges.map((r) => <ChoicePill key={r.key} label={r.label} active={range === r.key} onClick={() => setRange(r.key)} />)}
          </div>
          <div className="w-44">
            <label htmlFor="pz-camera" className="field-label">Camera</label>
            <select id="pz-camera" className="field" value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">All cameras</option>
              {cameras.data?.map((c) => <option key={c.camera_id} value={c.camera_id}>{c.name}</option>)}
            </select>
          </div>
          <div className="w-44">
            <label htmlFor="pz-state" className="field-label">Status</label>
            <select id="pz-state" className="field" value={state} onChange={(e) => setState(e.target.value)}>
              <option value="">Any status</option>
              {STATES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
        </div>
      </Card>

      <Card title="Pauses (newest first)">
        {pauses.error ? <ErrorState message={pauses.error} onRetry={reload} />
          : !pauses.data ? <Spinner />
          : pauses.data.length === 0 ? (
            <EmptyState title="No pauses in this period" hint="Monitoring was running the whole time." />
          ) : (
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[62rem] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs font-medium tracking-wide text-slate-500 uppercase">
                  <tr>
                    <th scope="col" className="px-5 py-3">Camera</th>
                    <th scope="col" className="px-4 py-3">Reason</th>
                    <th scope="col" className="px-4 py-3">From</th>
                    <th scope="col" className="px-4 py-3">To</th>
                    <th scope="col" className="px-4 py-3">How long</th>
                    <th scope="col" className="px-4 py-3">Who</th>
                    <th scope="col" className="px-4 py-3">Status</th>
                    <th scope="col" className="px-5 py-3 text-right"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {pauses.data.map((p) => {
                    const st = STATES.find((s) => s.value === p.state)!;
                    return (
                      <tr key={p.pause_id} className={p.state === "active" ? "bg-amber-50/50" : ""}>
                        <td className="px-5 py-3 font-medium whitespace-nowrap text-slate-900">{p.camera_name}</td>
                        <td className="px-4 py-3 text-slate-700">
                          {p.reason_label}
                          {p.note && <span className="block text-xs text-slate-500">{p.note}</span>}
                        </td>
                        <td className="px-4 py-3 whitespace-nowrap text-slate-700">{formatDateTime(p.starts_at)}</td>
                        <td className="px-4 py-3 whitespace-nowrap text-slate-700">{toText(p)}</td>
                        <td className="px-4 py-3 whitespace-nowrap text-slate-600">{duration(p, now)}</td>
                        <td className="px-4 py-3 text-slate-700">
                          {p.created_by_name ?? "—"}
                          {p.ended_by_name && (
                            <span className="block text-xs text-slate-500">
                              {p.state === "cancelled" ? "Cancelled" : "Resumed"} by {p.ended_by_name}
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset ${st.style}`}>
                            {st.label}
                          </span>
                        </td>
                        <td className="px-5 py-3 text-right">
                          {(p.state === "active" || p.state === "planned") && (
                            <button type="button" className="btn-secondary !py-1 text-xs whitespace-nowrap"
                              onClick={() => setDialog({ kind: "end", pause: p })}>
                              {p.state === "active" ? "Resume now" : "Cancel"}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      {dialog?.kind === "new" && cameras.data && (
        <PauseDialog cameras={cameras.data} cameraId={cameras.data[0]?.camera_id ?? null}
          onClose={() => { setDialog(null); reload(); }} />
      )}
      {dialog?.kind === "end" && (
        <EndPauseDialog pause={dialog.pause} cameraName={dialog.pause.camera_name}
          onClose={() => { setDialog(null); reload(); }} />
      )}
    </div>
  );
}
