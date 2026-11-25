import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { CheckIcon, PlusIcon } from "../components/Icons";
import { LiveStream } from "../components/LiveStream";
import { Modal } from "../components/Modal";
import { ZONE_STYLE } from "../components/ZoneCanvas";
import { Card, CameraStatusBadge, EmptyState, ErrorState, Spinner } from "../components/ui";
import { ApiError, SAVED_MESSAGE, apiWithHeaders, workerReload } from "../lib/api";
import {
  cameraPayload, emptyCameraForm, formFromCamera, maskRtsp, validateCameraForm, type CameraFormErrors,
  type CameraFormValues,
} from "../lib/cameraForm";
import type { Camera, Zone } from "../lib/types";
import { useApi } from "../lib/useApi";
import { ZONE_TYPE_LABEL } from "../lib/zoneGeometry";
import { useLive } from "../live/LiveContext";

export type Notice = { tone: "ok" | "warn" | "error"; text: string } | null;

export function NoticeBar({ notice, onClose }: { notice: Notice; onClose: () => void }) {
  if (!notice) return null;
  const style = notice.tone === "ok" ? "border-emerald-200 bg-emerald-50 text-emerald-800"
    : notice.tone === "warn" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-red-200 bg-red-50 text-red-700";
  return (
    <div className={`flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm ${style}`}
      role={notice.tone === "error" ? "alert" : "status"}>
      <span className="flex items-start gap-2">
        {notice.tone === "ok" && <CheckIcon className="mt-0.5 h-4 w-4 shrink-0" />}
        {notice.text}
      </span>
      <button type="button" className="shrink-0 hover:underline" onClick={onClose}>Dismiss</button>
    </div>
  );
}

function Field({ id, label, hint, error, children }:
  { id: string; label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="field-label">{label}</label>
      {children}
      {error ? <p className="field-error">{error}</p> : hint ? <p className="field-hint">{hint}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------- add / edit camera

function CameraForm({ camera, onClose, onSaved }:
  { camera?: Camera; onClose: () => void; onSaved: (c: Camera, notice: Notice) => void }) {
  const [v, setV] = useState<CameraFormValues>(() => (camera ? formFromCamera(camera) : emptyCameraForm()));
  const [errors, setErrors] = useState<CameraFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof CameraFormValues>(k: K, value: CameraFormValues[K]) => {
    setV((x) => ({ ...x, [k]: value }));
    setErrors((e) => ({ ...e, [k]: undefined }));
  };
  const cls = (k: keyof CameraFormValues) => `field ${errors[k] ? "field-invalid" : ""}`;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const found = validateCameraForm(v);
    setErrors(found);
    if (Object.keys(found).length) return;
    const body = cameraPayload(v, camera);
    if (camera && Object.keys(body).length === 0) return onClose();
    setSaving(true);
    try {
      const { data, headers } = camera
        ? await apiWithHeaders<Camera>(`/api/cameras/${camera.camera_id}`, { method: "PATCH", body })
        : await apiWithHeaders<Camera>("/api/cameras", { method: "POST", body });
      const reload = workerReload(headers);
      onSaved(data, { tone: reload === "failed" ? "warn" : "ok", text: SAVED_MESSAGE[reload] });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "The camera could not be saved.");
      setSaving(false);
    }
  }

  return (
    <Modal title={camera ? `Edit ${camera.name}` : "Add a camera"} onClose={onClose} busy={saving} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
        <button type="submit" form="camera-form" className="btn-primary" disabled={saving}>
          {saving ? "Saving…" : camera ? "Save changes" : "Add camera"}
        </button>
      </>
    }>
      <form id="camera-form" onSubmit={submit} noValidate className="space-y-4">
        {formError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{formError}</p>}
        <Field id="c-name" label="Name" error={errors.name} hint="Shown everywhere in RADS, e.g. Camera 3 (fryer)">
          <input id="c-name" className={cls("name")} value={v.name} autoFocus onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field id="c-url" label="Camera address (RTSP)" error={errors.rtsp_url}
          hint="From the camera's network settings. It contains the camera password, so only administrators and technical staff see it.">
          <input id="c-url" className={`${cls("rtsp_url")} font-mono text-xs`} value={v.rtsp_url} autoComplete="off"
            spellCheck={false} placeholder="rtsp://user:password@192.168.1.64:554/Streaming/Channels/101"
            onChange={(e) => set("rtsp_url", e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field id="c-w" label="Picture width" error={errors.frame_width}>
            <input id="c-w" inputMode="numeric" className={cls("frame_width")} value={v.frame_width}
              onChange={(e) => set("frame_width", e.target.value)} />
          </Field>
          <Field id="c-h" label="Picture height" error={errors.frame_height}>
            <input id="c-h" inputMode="numeric" className={cls("frame_height")} value={v.frame_height}
              onChange={(e) => set("frame_height", e.target.value)} />
          </Field>
          <Field id="c-fps" label="Pictures checked per second" error={errors.target_fps}>
            <input id="c-fps" inputMode="decimal" className={cls("target_fps")} value={v.target_fps}
              onChange={(e) => set("target_fps", e.target.value)} />
          </Field>
        </div>
        <p className="field-hint !mt-1">
          The picture size is the camera's full resolution (for these cameras 2688 × 1520). Area outlines are saved in
          these pixels.
        </p>
        <label className="flex items-start gap-3">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-brand-600" checked={v.enabled}
            onChange={(e) => set("enabled", e.target.checked)} />
          <span>
            <span className="block text-sm font-medium text-slate-800">Camera turned on</span>
            <span className="block text-xs text-slate-500">When off, the camera system does not watch this camera.</span>
          </span>
        </label>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- page

function Address({ url }: { url?: string }) {
  const [shown, setShown] = useState(false);
  if (!url) return <span className="text-slate-400">—</span>;
  return (
    <span className="flex items-center gap-2">
      <code className="truncate font-mono text-xs text-slate-700" title={shown ? url : undefined}>
        {shown ? url : maskRtsp(url)}
      </code>
      {url !== maskRtsp(url) && (
        <button type="button" className="shrink-0 text-xs font-medium text-brand-700 hover:underline"
          onClick={() => setShown((s) => !s)} aria-label={shown ? "Hide the camera password" : "Show the camera password"}>
          {shown ? "Hide" : "Show"}
        </button>
      )}
    </span>
  );
}

function AreaSummary({ zones }: { zones: Zone[] }) {
  if (zones.length === 0) return <span className="text-slate-400">None yet</span>;
  return (
    <ul className="space-y-0.5">
      {zones.map((z) => (
        <li key={z.zone_id} className={`flex items-center gap-2 text-xs ${z.active ? "text-slate-700" : "text-slate-400"}`}
          title={ZONE_TYPE_LABEL[z.type]}>
          <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${ZONE_STYLE[z.type].swatch}`} />
          {z.name}{z.active ? "" : " (inactive)"}
        </li>
      ))}
    </ul>
  );
}

export default function CamerasPage() {
  const { hasRole } = useAuth();
  const canEdit = hasRole("Admin", "Technical");
  const cameras = useApi<Camera[]>("/api/cameras");
  const zones = useApi<Zone[]>("/api/zones");
  const { cameras: live } = useLive();
  const [editing, setEditing] = useState<Camera | "new" | null>(null);
  const [watching, setWatching] = useState<Camera | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  return (
    <div className="space-y-4">
      <NoticeBar notice={notice} onClose={() => setNotice(null)} />
      <Card title="Cameras" action={canEdit && (
        <button type="button" className="btn-primary shrink-0 whitespace-nowrap !py-1.5"
          onClick={() => { setNotice(null); setEditing("new"); }}>
          <PlusIcon className="h-4 w-4" /> Add camera
        </button>
      )}>
        {cameras.error ? <ErrorState message={cameras.error} onRetry={cameras.reload} />
          : !cameras.data ? <Spinner />
          : cameras.data.length === 0 ? <EmptyState title="No cameras yet" hint="Add the kitchen cameras to start." />
          : (
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[60rem] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs font-medium tracking-wide text-slate-500
                  uppercase">
                  <tr>
                    <th scope="col" className="px-5 py-3">Camera</th>
                    <th scope="col" className="px-4 py-3">Status</th>
                    <th scope="col" className="px-4 py-3">Picture size</th>
                    <th scope="col" className="px-4 py-3">Camera address</th>
                    <th scope="col" className="px-4 py-3">Areas</th>
                    <th scope="col" className="px-5 py-3 text-right"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {cameras.data.map((c) => {
                    const status = c.enabled ? live[c.camera_id]?.status ?? c.status : "offline";
                    return (
                      <tr key={c.camera_id} className="align-top">
                        <td className="px-5 py-3">
                          <p className="font-medium text-slate-900">{c.name}</p>
                          <p className="text-xs text-slate-500">
                            {c.enabled ? `Turned on · ${c.target_fps} pictures/s checked` : "Turned off"}
                          </p>
                        </td>
                        <td className="px-4 py-3"><CameraStatusBadge status={status} /></td>
                        <td className="px-4 py-3 whitespace-nowrap text-slate-700">
                          {c.frame_width && c.frame_height ? `${c.frame_width} × ${c.frame_height}`
                            : <span className="text-amber-700">Not set</span>}
                        </td>
                        <td className="max-w-[18rem] px-4 py-3"><Address url={c.rtsp_url} /></td>
                        <td className="px-4 py-3">
                          {zones.data ? <AreaSummary zones={zones.data.filter((z) => z.camera_id === c.camera_id)} />
                            : <span className="text-slate-400">…</span>}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex justify-end gap-2">
                            <button type="button" className="btn-secondary !py-1 text-xs" onClick={() => setWatching(c)}
                              aria-label={`Live view of ${c.name}`}>
                              Live
                            </button>
                            <Link to={`/cameras/${c.camera_id}`} className="btn-secondary !py-1 text-xs"
                              aria-label={`Areas of ${c.name}`}>
                              Areas
                            </Link>
                            {canEdit && (
                              <button type="button" className="btn-secondary !py-1 text-xs"
                                onClick={() => { setNotice(null); setEditing(c); }} aria-label={`Edit ${c.name}`}>
                                Edit
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      <p className="text-xs text-slate-500">
        Online / offline comes from the camera system's heartbeat every 5 seconds. Areas are where the rules are
        checked; open <span className="font-medium">Areas</span> to draw or change them on the camera picture.
      </p>

      {editing && (
        <CameraForm camera={editing === "new" ? undefined : editing} onClose={() => setEditing(null)}
          onSaved={(_, n) => { setEditing(null); setNotice(n); cameras.reload(); }} />
      )}
      {watching && (
        <Modal title={`Live view – ${watching.name}`} onClose={() => setWatching(null)} size="xl" variant="image">
          <LiveStream cameraId={watching.camera_id} name={watching.name}
            aspect={watching.frame_width && watching.frame_height ? watching.frame_width / watching.frame_height : 16 / 9} />
        </Modal>
      )}
    </div>
  );
}
