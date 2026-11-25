import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeftIcon, PlusIcon } from "../components/Icons";
import { Modal } from "../components/Modal";
import { ZONE_STYLE, ZoneCanvas, type Picture } from "../components/ZoneCanvas";
import { Card, CameraStatusBadge, EmptyState, ErrorState, Spinner } from "../components/ui";
import { ApiError, SAVED_MESSAGE, apiBlob, apiWithHeaders, workerReload } from "../lib/api";
import type { Camera, Point, Rule, Zone, ZoneType } from "../lib/types";
import { useApi } from "../lib/useApi";
import { ZONE_TYPE_HINT, ZONE_TYPE_LABEL, outlineProblem } from "../lib/zoneGeometry";
import { useLive } from "../live/LiveContext";
import { NoticeBar, type Notice } from "./CamerasPage";
import { NotFound } from "./Placeholders";

const TYPES: ZoneType[] = ["prep", "waste", "ignore"];

/** The camera's latest picture, taken from the live stream relay. Kept on screen while a newer one loads. */
function useSnapshot(cameraId: number | null): [Picture, () => void] {
  const [picture, setPicture] = useState<Picture>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const current = useRef<string | null>(null);

  useEffect(() => {
    if (cameraId === null) return;
    const ctrl = new AbortController();
    apiBlob(`/api/cameras/${cameraId}/snapshot`, { signal: ctrl.signal })
      .then(({ blob }) => {
        const url = URL.createObjectURL(blob);
        if (current.current) URL.revokeObjectURL(current.current);
        current.current = url;
        setPicture({ status: "ready", url });
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        const status = e instanceof ApiError ? e.status : 0;
        setPicture(status === 503 ? { status: "offline", message: "The camera system is offline." }
          : status === 404 ? { status: "off", message: "This camera is turned off." }
          : { status: "error", message: e instanceof ApiError ? e.message : "The picture could not be loaded." });
      });
    return () => ctrl.abort();
  }, [cameraId, attempt]);

  useEffect(() => () => {
    if (current.current) URL.revokeObjectURL(current.current);
  }, []);
  return [picture, useCallback(() => setAttempt((n) => n + 1), [])];
}

/** `lockedTo`: rules use this area, so only its current type is allowed (the server would refuse the others). */
function TypeChoice({ value, onChange, name, lockedTo }:
  { value: ZoneType; onChange: (t: ZoneType) => void; name: string; lockedTo?: ZoneType }) {
  return (
    <fieldset className="space-y-2">
      <legend className="field-label">Type of area</legend>
      {TYPES.map((t) => {
        const off = lockedTo !== undefined && t !== lockedTo;
        return (
        <label key={t} className={`flex items-start gap-3 rounded-lg border px-3 py-2 transition ${
          off ? "cursor-not-allowed border-slate-200 opacity-50"
            : value === t ? "cursor-pointer border-brand-400 bg-brand-50" : "cursor-pointer border-slate-200 hover:bg-slate-50"}`}>
          <input type="radio" name={name} className="mt-1 accent-brand-600" checked={value === t} disabled={off}
            onChange={() => onChange(t)} />
          <span className={`mt-1 h-3 w-3 shrink-0 rounded-sm ${t === "ignore" ? "hatch-swatch" : ZONE_STYLE[t].swatch}`} />
          <span>
            <span className="block text-sm font-medium text-slate-900">{ZONE_TYPE_LABEL[t]}</span>
            <span className="block text-xs text-slate-500">{ZONE_TYPE_HINT[t]}</span>
          </span>
        </label>
        );
      })}
    </fieldset>
  );
}

export default function CameraAreasPage() {
  const { id } = useParams();
  const valid = !!id && /^\d+$/.test(id);
  const cameraId = valid ? Number(id) : null;
  const camera = useApi<Camera>(valid ? `/api/cameras/${id}` : null);
  const zones = useApi<Zone[]>(valid ? "/api/zones" : null, { camera_id: id });
  const rules = useApi<Rule[]>(valid ? "/api/rules" : null, { camera_id: id });
  const [picture, refreshPicture] = useSnapshot(cameraId);
  const { cameras: live } = useLive();

  const [mode, setMode] = useState<"view" | "draw" | "shape">("view");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Point[]>([]);
  const [history, setHistory] = useState<Point[][]>([]);
  const dragging = useRef(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<ZoneType>("prep");
  const [form, setForm] = useState<{ name: string; type: ZoneType; active: boolean } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<Zone | null>(null);
  const [aspectWarning, setAspectWarning] = useState<string | null>(null);

  const cam = camera.data;
  const W = cam?.frame_width ?? null;
  const H = cam?.frame_height ?? null;
  const list = zones.data ?? [];
  const selected = list.find((z) => z.zone_id === selectedId) ?? null;

  // Selecting an area fills the details form.
  useEffect(() => {
    setForm(selected ? { name: selected.name, type: selected.type, active: selected.active } : null);
    setProblem(null);
  }, [selected?.zone_id, selected?.name, selected?.type, selected?.active]); // eslint-disable-line react-hooks/exhaustive-deps

  // Warn if the picture's shape does not match the camera's picture size (areas would not line up).
  useEffect(() => {
    setAspectWarning(null);
    if (picture.status !== "ready" || !W || !H) return;
    const img = new Image();
    img.onload = () => {
      const ratio = img.naturalWidth / img.naturalHeight;
      if (Math.abs(ratio - W / H) / (W / H) > 0.02) {
        setAspectWarning(`The camera picture is ${img.naturalWidth} × ${img.naturalHeight}, which is not the same shape as `
          + `the picture size set for this camera (${W} × ${H}). Check the camera's picture size, or areas will not line up.`);
      }
    };
    img.src = picture.url;
  }, [picture, W, H]);

  const undo = useCallback(() => {
    setHistory((h) => {
      if (h.length === 0) return h;
      setDraft(h[h.length - 1]);
      return h.slice(0, -1);
    });
    setProblem(null);
  }, []);

  const cancel = useCallback(() => {
    setMode("view");
    setDraft([]);
    setHistory([]);
    setProblem(null);
  }, []);

  // Esc closes the "Selected area" panel (a dialog on top handles Esc itself and stops it first).
  useEffect(() => {
    if (mode !== "view" || selectedId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, selectedId]);

  useEffect(() => {
    if (mode === "view") return;
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select");
      if (typing) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
      } else if (e.key === "Escape") cancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, undo, cancel]);

  if (!valid) return <NotFound />;

  const ruleNames = (z: Zone) => (rules.data ?? []).filter((r) => z.rule_ids.includes(r.rule_id)).map((r) => r.hazard_name);

  async function save(path: string, method: "POST" | "PATCH", body: Record<string, unknown>, after?: (z: Zone) => void) {
    setBusy(true);
    setProblem(null);
    try {
      const { data, headers } = await apiWithHeaders<Zone>(path, { method, body });
      const reload = workerReload(headers);
      setNotice({ tone: reload === "failed" ? "warn" : "ok", text: SAVED_MESSAGE[reload] });
      zones.reload();
      rules.reload();
      refreshPicture();
      after?.(data);
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "The area could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  function saveNew() {
    const name = newName.trim();
    const outline = outlineProblem(draft, W!, H!);
    if (!name) return setProblem("Give the area a name, for example Prep table 2.");
    if (outline) return setProblem(outline);
    void save("/api/zones", "POST", { camera_id: cameraId, name, type: newType, polygon: draft, active: true }, (z) => {
      cancel();
      setSelectedId(z.zone_id);
    });
  }

  function saveShape() {
    const outline = outlineProblem(draft, W!, H!);
    if (outline) return setProblem(outline);
    void save(`/api/zones/${selectedId}`, "PATCH", { polygon: draft }, () => cancel());
  }

  function saveDetails() {
    if (!selected || !form) return;
    const body: Record<string, unknown> = {};
    if (form.name.trim() !== selected.name) body.name = form.name.trim();
    if (form.type !== selected.type) body.type = form.type;
    if (form.active !== selected.active) body.active = form.active;
    if (!form.name.trim()) return setProblem("The name cannot be empty.");
    if (Object.keys(body).length === 0) return setSelectedId(null); // nothing changed: just close
    void save(`/api/zones/${selected.zone_id}`, "PATCH", body, () => setSelectedId(null));
  }

  /** Change a field of the selected area; an old error no longer applies. */
  const edit = (patch: Partial<{ name: string; type: ZoneType; active: boolean }>) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
    setProblem(null);
  };

  const startDrawing = () => {
    setNotice(null);
    setSelectedId(null);
    setDraft([]);
    setHistory([]);
    setNewName("");
    setNewType("prep");
    setProblem(null);
    setMode("draw");
  };

  const status = cam ? (cam.enabled ? live[cam.camera_id]?.status ?? cam.status : "offline") : "offline";

  return (
    <div className="space-y-4">
      <Link to="/cameras" className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline">
        <ArrowLeftIcon className="h-4 w-4" /> All cameras
      </Link>

      {camera.error ? <Card><ErrorState message={camera.error} onRetry={camera.reload} /></Card>
        : !cam ? <Card><Spinner label="Loading camera…" /></Card>
        : (
          <>
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3 p-5">
                <div>
                  <h2 className="text-xl font-semibold text-slate-900">Areas on {cam.name}</h2>
                  <p className="mt-1 text-sm text-slate-600">
                    Outlines are saved in camera pixels{W && H ? ` (${W} × ${H})` : ""}, whatever size the picture is
                    shown at.
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <CameraStatusBadge status={status} />
                  <button type="button" className="btn-secondary" onClick={refreshPicture} disabled={busy}>
                    Refresh picture
                  </button>
                </div>
              </div>
            </Card>

            <NoticeBar notice={notice} onClose={() => setNotice(null)} />

            {!W || !H ? (
              <Card>
                <EmptyState title="This camera's picture size is not set"
                  hint="Set it with Edit on the cameras list (for these cameras 2688 × 1520) before drawing areas." />
              </Card>
            ) : (
              <div className="grid gap-4 lg:grid-cols-3">
                <div className="space-y-3 lg:col-span-2">
                  <Card>
                    <div className="space-y-3 p-4">
                      <ZoneCanvas frameW={W} frameH={H} picture={picture} zones={list} selectedId={selectedId}
                        mode={mode} draft={draft} draftType={mode === "draw" ? newType : selected?.type ?? "prep"}
                        onSelect={(zid) => { setNotice(null); setSelectedId(zid); }}
                        onAddPoint={(p) => { setHistory((h) => [...h, draft]); setDraft((d) => [...d, p]); setProblem(null); }}
                        onMovePoint={(i, p) => {
                          if (!dragging.current) {
                            dragging.current = true;
                            setHistory((h) => [...h, draft]);
                          }
                          setDraft((d) => d.map((q, k) => (k === i ? p : q)));
                        }}
                        onMoveEnd={() => { dragging.current = false; }}
                        onCloseOutline={saveNew} />
                      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-600">
                        {TYPES.map((t) => (
                          <span key={t} className="flex items-center gap-1.5">
                            <span className={`h-3 w-3 rounded-sm ${t === "ignore" ? "hatch-swatch" : ZONE_STYLE[t].swatch}`} />
                            {ZONE_TYPE_LABEL[t]}
                          </span>
                        ))}
                        <span className="text-slate-400">Dashed outline = inactive</span>
                      </div>
                      {aspectWarning && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">{aspectWarning}</p>}
                    </div>
                  </Card>
                </div>

                <div className="space-y-4">
                  {mode === "draw" && (
                    <Card title="Draw a new area">
                      <div className="space-y-4 p-5">
                        <ol className="list-decimal space-y-1 pl-4 text-sm text-slate-600">
                          <li>Click on the picture to place the corners, in order around the area.</li>
                          <li>Drag a corner to move it. Undo removes the last change.</li>
                          <li>Click the first corner or Save to finish.</li>
                        </ol>
                        <div>
                          <label htmlFor="z-new-name" className="field-label">Name</label>
                          <input id="z-new-name" className="field" value={newName} placeholder="e.g. Prep table 2"
                            onChange={(e) => setNewName(e.target.value)} maxLength={60} />
                        </div>
                        <TypeChoice value={newType} onChange={setNewType} name="z-new-type" />
                        <p className="text-xs text-slate-500">{draft.length} corner{draft.length === 1 ? "" : "s"} placed</p>
                        {problem && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{problem}</p>}
                        <div className="flex flex-wrap gap-2">
                          <button type="button" className="btn-secondary" onClick={undo} disabled={history.length === 0 || busy}>
                            Undo last point
                          </button>
                          <button type="button" className="btn-secondary" onClick={cancel} disabled={busy}>Cancel</button>
                          <button type="button" className="btn-primary ml-auto" onClick={saveNew} disabled={busy || draft.length < 3}>
                            {busy ? "Saving…" : "Save area"}
                          </button>
                        </div>
                      </div>
                    </Card>
                  )}

                  {mode === "shape" && selected && (
                    <Card title={`Change the outline of ${selected.name}`}>
                      <div className="space-y-4 p-5">
                        <p className="text-sm text-slate-600">Drag the round handles to move the corners.</p>
                        {problem && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{problem}</p>}
                        <div className="flex flex-wrap gap-2">
                          <button type="button" className="btn-secondary" onClick={undo} disabled={history.length === 0 || busy}>
                            Undo
                          </button>
                          <button type="button" className="btn-secondary" onClick={cancel} disabled={busy}>Cancel</button>
                          <button type="button" className="btn-primary ml-auto" onClick={saveShape} disabled={busy}>
                            {busy ? "Saving…" : "Save outline"}
                          </button>
                        </div>
                      </div>
                    </Card>
                  )}

                  {mode === "view" && selected && form && (
                    <Card title="Selected area" action={
                      <button type="button" aria-label="Close" title="Close (Esc)" disabled={busy}
                        onClick={() => setSelectedId(null)}
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                        <svg viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor" aria-hidden="true">
                          <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4Z" />
                        </svg>
                      </button>
                    }>
                      <div className="space-y-4 p-5">
                        <div>
                          <label htmlFor="z-name" className="field-label">Name</label>
                          <input id="z-name" className="field" value={form.name} maxLength={60}
                            onChange={(e) => edit({ name: e.target.value })} />
                        </div>
                        <TypeChoice value={form.type} onChange={(t) => edit({ type: t })} name="z-type"
                          lockedTo={selected.rule_ids.length > 0 ? selected.type : undefined} />
                        {selected.rule_ids.length > 0 && (
                          <p className="text-xs text-slate-500">
                            Used by: {ruleNames(selected).join(", ") || `${selected.rule_ids.length} rule(s)`}. To change
                            the type, first remove this area from those rules on{" "}
                            <Link to="/rules" className="font-medium text-brand-700 hover:underline">Detection rules</Link>.
                          </p>
                        )}
                        <label className="flex items-start gap-3">
                          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-brand-600" checked={form.active}
                            onChange={(e) => edit({ active: e.target.checked })} />
                          <span>
                            <span className="block text-sm font-medium text-slate-800">Active</span>
                            <span className="block text-xs text-slate-500">
                              When inactive, the camera system does not use this area.
                            </span>
                          </span>
                        </label>
                        {problem && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{problem}</p>}
                        <div className="flex flex-wrap gap-2">
                          <button type="button" className="btn-primary" onClick={saveDetails} disabled={busy}>
                            {busy ? "Saving…" : "Save changes"}
                          </button>
                          <button type="button" className="btn-secondary" disabled={busy} onClick={() => {
                            setNotice(null); setDraft(selected.polygon.map((p) => [...p] as Point)); setHistory([]); setMode("shape");
                          }}>
                            Change outline
                          </button>
                          <button type="button" className="btn-secondary ml-auto text-red-700 hover:!bg-red-50"
                            disabled={busy} onClick={() => setDeleting(selected)}>
                            Delete
                          </button>
                        </div>
                      </div>
                    </Card>
                  )}

                  <Card title="Areas on this camera" action={mode === "view" && (
                    <button type="button" className="btn-primary shrink-0 whitespace-nowrap !py-1.5" onClick={startDrawing}>
                      <PlusIcon className="h-4 w-4" /> Add area
                    </button>
                  )}>
                    {zones.error ? <ErrorState message={zones.error} onRetry={zones.reload} />
                      : !zones.data ? <Spinner />
                      : list.length === 0 ? <EmptyState title="No areas yet" hint="Add the food preparation area first." />
                      : (
                        <ul className="divide-y divide-slate-100">
                          {list.map((z) => (
                            <li key={z.zone_id}>
                              <button type="button" disabled={mode !== "view"}
                                onClick={() => { setNotice(null); setSelectedId(z.zone_id === selectedId ? null : z.zone_id); }}
                                className={`flex w-full items-center gap-3 px-5 py-3 text-left transition disabled:cursor-default ${
                                  z.zone_id === selectedId ? "bg-brand-50" : "hover:bg-slate-50"}`}>
                                <span className={`h-3.5 w-3.5 shrink-0 rounded-sm ${z.type === "ignore" ? "hatch-swatch" : ZONE_STYLE[z.type].swatch}`} />
                                <span className="min-w-0 flex-1">
                                  <span className={`block truncate text-sm font-medium ${z.active ? "text-slate-900" : "text-slate-400"}`}>
                                    {z.name}
                                  </span>
                                  <span className="block text-xs text-slate-500">
                                    {ZONE_TYPE_LABEL[z.type]} · {z.polygon.length} corners
                                    {z.rule_ids.length ? ` · ${z.rule_ids.length} rule${z.rule_ids.length > 1 ? "s" : ""}` : ""}
                                  </span>
                                </span>
                                {!z.active && (
                                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Inactive</span>
                                )}
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                  </Card>
                </div>
              </div>
            )}
          </>
        )}

      {deleting && (
        <DeleteArea zone={deleting} onClose={() => setDeleting(null)}
          onDone={(n) => {
            setDeleting(null);
            setNotice(n);
            setSelectedId(null);
            zones.reload();
            rules.reload();
            refreshPicture();
          }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- delete (or deactivate when it has history)

const IN_USE = /^Incidents refer to this zone/;

function DeleteArea({ zone, onClose, onDone }: { zone: Zone; onClose: () => void; onDone: (n: Notice) => void }) {
  const [busy, setBusy] = useState(false);
  const [inUse, setInUse] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(deactivate: boolean) {
    setBusy(true);
    setError(null);
    try {
      const { headers } = deactivate
        ? await apiWithHeaders(`/api/zones/${zone.zone_id}`, { method: "PATCH", body: { active: false } })
        : await apiWithHeaders(`/api/zones/${zone.zone_id}`, { method: "DELETE" });
      const reload = workerReload(headers);
      const after = reload === "ok" ? " The camera system has reloaded."
        : reload === "failed" ? " The camera system is offline; it will use the change when it restarts." : "";
      onDone({ tone: reload === "failed" ? "warn" : "ok",
        text: `${zone.name} was ${deactivate ? "deactivated" : "deleted"}.${after}` });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && IN_USE.test(e.message)) setInUse(true);
      else setError(e instanceof ApiError ? e.message : "The area could not be deleted.");
      setBusy(false);
    }
  }

  return (
    <Modal title={inUse ? `${zone.name} cannot be deleted` : `Delete ${zone.name}?`} size="sm" onClose={onClose} busy={busy}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy} data-autofocus>Cancel</button>
          {inUse ? (
            <button type="button" className="btn-primary" onClick={() => run(true)} disabled={busy}>
              {busy ? "Saving…" : "Deactivate instead"}
            </button>
          ) : (
            <button type="button" className="btn-danger" onClick={() => run(false)} disabled={busy}>
              {busy ? "Deleting…" : "Delete area"}
            </button>
          )}
        </>
      }>
      <div className="space-y-3 text-sm text-slate-600">
        {inUse ? (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-900" role="alert">
            Incidents refer to this area, so it cannot be deleted: their records still need it. You can deactivate it
            instead. The camera system then stops using it, and old incidents keep their area.
          </p>
        ) : (
          <p>The outline is removed for good. If incidents were recorded in this area, you will be offered to deactivate it instead.</p>
        )}
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">{error}</p>}
      </div>
    </Modal>
  );
}
