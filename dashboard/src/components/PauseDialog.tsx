// The "Pause monitoring" dialog, and the confirmation to resume an active pause or cancel a planned one.
import { useState, type FormEvent } from "react";
import { ApiError, apiWithHeaders, workerReload } from "../lib/api";
import {
  PAUSE_LENGTHS, PAUSE_REASONS, emptyPauseForm, pausePayload, pauseSummary, pausedLine, plannedLine,
  validatePauseForm, type PauseFormErrors, type PauseFormValues,
} from "../lib/pauseForm";
import type { Pause } from "../lib/types";
import { useMonitoring } from "../live/MonitoringContext";
import { InfoIcon } from "./Icons";
import { Modal } from "./Modal";
import { useToasts } from "./Toasts";

const OFFLINE_NOTE = " The camera system is offline; it will follow this when it restarts.";

function Choice({ name, checked, onChange, label }:
  { name: string; checked: boolean; onChange: () => void; label: string }) {
  return (
    <label className={`cursor-pointer rounded-full px-3 py-1.5 text-sm ring-1 ring-inset transition ${
      checked ? "bg-brand-600 font-medium text-white ring-brand-600" : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50"}`}>
      <input type="radio" name={name} className="sr-only" checked={checked} onChange={onChange} />
      {label}
    </label>
  );
}

export function PauseDialog({ cameras, cameraId, onClose }:
  { cameras: { camera_id: number; name: string }[]; cameraId: number | null; onClose: () => void }) {
  const { reload } = useMonitoring();
  const { push } = useToasts();
  const [v, setV] = useState<PauseFormValues>(() => emptyPauseForm(cameraId));
  const [errors, setErrors] = useState<PauseFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof PauseFormValues>(k: K, value: PauseFormValues[K]) => {
    setV((x) => ({ ...x, [k]: value }));
    setErrors((e) => ({ ...e, [k]: undefined }));
    setFormError(null);
  };
  const cameraName = v.cameraId === null ? null : cameras.find((c) => c.camera_id === v.cameraId)?.name ?? null;
  const summary = pauseSummary(v, cameraName);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found = validatePauseForm(v);
    setErrors(found);
    if (Object.keys(found).length) return;
    setSaving(true);
    try {
      const { headers } = await apiWithHeaders<Pause>("/api/pauses", { method: "POST", body: pausePayload(v) });
      push({ title: v.when === "now" ? "Detection paused" : "Pause planned",
        body: summary.split(". ")[0] + "." + (workerReload(headers) === "failed" ? OFFLINE_NOTE : "") });
      reload();
      onClose();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "The pause could not be saved.");
      setSaving(false);
    }
  }

  return (
    <Modal title="Pause monitoring" onClose={onClose} busy={saving} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
        <button type="submit" form="pause-form" className="btn-primary" disabled={saving}>
          {saving ? "Saving…" : v.when === "now" ? "Pause detection" : "Plan the pause"}
        </button>
      </>
    }>
      <form id="pause-form" onSubmit={submit} noValidate className="space-y-5">
        {formError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{formError}</p>}
        <div>
          <label htmlFor="p-camera" className="field-label">Camera</label>
          <select id="p-camera" className="field" value={v.cameraId ?? "all"}
            onChange={(e) => set("cameraId", e.target.value === "all" ? null : Number(e.target.value))}>
            {cameras.map((c) => <option key={c.camera_id} value={c.camera_id}>{c.name}</option>)}
            <option value="all">All cameras</option>
          </select>
        </div>

        <fieldset>
          <legend className="field-label">Reason</legend>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {PAUSE_REASONS.map((r) => (
              <label key={r.value} className={`flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 text-sm ${
                v.reason === r.value ? "border-brand-400 bg-brand-50 text-slate-900" : "border-slate-200 text-slate-700 hover:bg-slate-50"}`}>
                <input type="radio" name="p-reason" className="accent-brand-600" checked={v.reason === r.value}
                  onChange={() => set("reason", r.value)} />
                {r.label}
              </label>
            ))}
          </div>
          {errors.reason && <p className="field-error">{errors.reason}</p>}
        </fieldset>

        <div>
          <label htmlFor="p-note" className="field-label">Note {v.reason === "other" ? "(needed for Other)" : "(optional)"}</label>
          <textarea id="p-note" className={`field min-h-16 ${errors.note ? "field-invalid" : ""}`} maxLength={500}
            value={v.note} onChange={(e) => set("note", e.target.value)} placeholder="e.g. deep clean of the prep table" />
          {errors.note && <p className="field-error">{errors.note}</p>}
        </div>

        <fieldset>
          <legend className="field-label">When</legend>
          <div className="flex flex-wrap gap-2">
            <Choice name="p-when" checked={v.when === "now"} onChange={() => set("when", "now")} label="Now" />
            <Choice name="p-when" checked={v.when === "planned"} onChange={() => set("when", "planned")} label="Planned" />
          </div>
          {v.when === "planned" && (
            <div className="mt-2 max-w-xs">
              <label htmlFor="p-start" className="field-label">Starts (Pakistan time)</label>
              <input id="p-start" type="datetime-local" className={`field ${errors.startAt ? "field-invalid" : ""}`}
                value={v.startAt} onChange={(e) => set("startAt", e.target.value)} />
              {errors.startAt && <p className="field-error">{errors.startAt}</p>}
            </div>
          )}
        </fieldset>

        <fieldset>
          <legend className="field-label">How long</legend>
          <div className="flex flex-wrap gap-2">
            {PAUSE_LENGTHS.map((l) => (
              <Choice key={l.value} name="p-length" checked={v.length === l.value} onChange={() => set("length", l.value)}
                label={l.label} />
            ))}
          </div>
          {v.length === "until" && (
            <div className="mt-2 max-w-xs">
              <label htmlFor="p-until" className="field-label">Until (Pakistan time)</label>
              <input id="p-until" type="datetime-local" className={`field ${errors.untilAt ? "field-invalid" : ""}`}
                value={v.untilAt} onChange={(e) => set("untilAt", e.target.value)} />
              {errors.untilAt && <p className="field-error">{errors.untilAt}</p>}
            </div>
          )}
        </fieldset>

        <p className="flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-3 text-sm text-amber-900"
          role="status" aria-live="polite">
          <InfoIcon className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <span data-testid="pause-summary">{summary}</span>
        </p>
      </form>
    </Modal>
  );
}

export function EndPauseDialog({ pause, cameraName, onClose }:
  { pause: Pause; cameraName: string; onClose: () => void }) {
  const { reload } = useMonitoring();
  const { push } = useToasts();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const planned = pause.state === "planned";
  const where = pause.camera_id === null ? "all cameras" : cameraName;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const { headers } = await apiWithHeaders<Pause>(`/api/pauses/${pause.pause_id}/end`, { method: "POST" });
      push({ title: planned ? "Planned pause cancelled" : "Detection resumed",
        body: (planned ? `The pause on ${where} will not happen.` : `Detection is running again on ${where}.`)
          + (workerReload(headers) === "failed" ? OFFLINE_NOTE : "") });
      reload();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That did not work. Please try again.");
      setBusy(false);
    }
  }

  return (
    <Modal title={planned ? "Cancel the planned pause?" : `Resume detection on ${where} now?`} size="sm"
      onClose={onClose} busy={busy} footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy} data-autofocus>
            {planned ? "Keep it" : "Not now"}
          </button>
          <button type="button" className="btn-primary" onClick={run} disabled={busy}>
            {busy ? "Saving…" : planned ? "Cancel the pause" : "Resume now"}
          </button>
        </>
      }>
      <div className="space-y-3 text-sm text-slate-600">
        <p className="font-medium text-slate-800">
          {pause.camera_id === null ? "All cameras" : cameraName}: {planned ? plannedLine(pause) : pausedLine(pause)}
        </p>
        <p>{planned ? "Detection will keep running at that time."
          : "Incidents and alerts will be created again from now on."}</p>
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">{error}</p>}
      </div>
    </Modal>
  );
}
