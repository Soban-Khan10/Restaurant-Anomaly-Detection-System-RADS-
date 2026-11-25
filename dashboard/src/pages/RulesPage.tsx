import { useState } from "react";
import { Link } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { ZONE_STYLE } from "../components/ZoneCanvas";
import { Card, EmptyState, ErrorState, SeverityBadge, Spinner } from "../components/ui";
import { ApiError, SAVED_MESSAGE, apiWithHeaders, workerReload } from "../lib/api";
import { SEVERITIES, formatPercent, formatSeconds } from "../lib/format";
import {
  RANGES, formFromRule, rulePatch, validateRuleForm, zoneTypeFor, type RuleFormErrors, type RuleFormValues,
} from "../lib/ruleForm";
import type { Camera, Hazard, Rule, Severity, Zone } from "../lib/types";
import { useApi } from "../lib/useApi";
import { ZONE_TYPE_LABEL } from "../lib/zoneGeometry";
import { NoticeBar, type Notice } from "./CamerasPage";

type NumberKey = keyof typeof RANGES;

// Same labels as the incident page's "Why it was raised".
const FIELDS: { key: NumberKey; label: string; hint: string }[] = [
  { key: "confidence", label: "Minimum confidence",
    hint: "Detections the system is less sure of are ignored. Higher means fewer false alarms but more missed cases." },
  { key: "window", label: "Time checked", hint: "How many seconds of pictures are looked at before deciding." },
  { key: "share", label: "Share of pictures needed", hint: "In that time, the hazard must be seen in at least this share of the pictures." },
  { key: "endGap", label: "Ends after not seen for", hint: "The incident ends when the hazard has not been seen for this long." },
  { key: "cooldown", label: "Wait before alerting again", hint: "After an incident, no new alert for the same person or area during this time." },
];

const SEVERITY_HINT = "How urgent the alert is shown to managers.";

function summary(v: RuleFormValues): string {
  const n = (s: string) => (Number.isFinite(Number(s)) && s.trim() !== "" ? Number(s) : NaN);
  const parts = [n(v.share), n(v.window), n(v.confidence), n(v.endGap), n(v.cooldown)];
  if (parts.some(Number.isNaN)) return "";
  return `Raises an incident when the hazard is seen in at least ${formatPercent(parts[0] / 100)} of the pictures over `
    + `${formatSeconds(parts[1])}, counting only detections the system is at least ${formatPercent(parts[2] / 100)} sure `
    + `of. It ends after ${formatSeconds(parts[3])} without it; then no new alert for ${formatSeconds(parts[4])}.`;
}

// ---------------------------------------------------------------- one rule

function RuleCard({ rule, zones, canEdit, onSaved }:
  { rule: Rule; zones: Zone[]; canEdit: boolean; onSaved: (r: Rule) => void }) {
  const [v, setV] = useState<RuleFormValues>(() => formFromRule(rule));
  const [errors, setErrors] = useState<RuleFormErrors>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const needs = zoneTypeFor(rule.hazard_id);
  const usable = zones.filter((z) => z.type === needs || rule.zone_ids.includes(z.zone_id));
  const body = rulePatch(v, rule);
  const dirty = Object.keys(body).length > 0;

  const set = <K extends keyof RuleFormValues>(k: K, value: RuleFormValues[K]) => {
    setV((x) => ({ ...x, [k]: value }));
    setErrors((e) => ({ ...e, [k]: undefined }));
    setNotice(null);
  };

  async function save() {
    const found = validateRuleForm(v);
    setErrors(found);
    if (Object.keys(found).length) return;
    setSaving(true);
    setNotice(null);
    try {
      const { data, headers } = await apiWithHeaders<Rule>(`/api/rules/${rule.rule_id}`, { method: "PATCH", body });
      const reload = workerReload(headers);
      onSaved(data);
      setV(formFromRule(data));
      setNotice({ tone: reload === "failed" ? "warn" : "ok", text: SAVED_MESSAGE[reload] });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "The rule could not be saved.";
      if (/zone/i.test(msg)) setErrors({ zoneIds: msg });
      else setNotice({ tone: "error", text: msg });
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={`flex flex-col rounded-xl border bg-white shadow-sm ${v.enabled ? "border-slate-200" : "border-dashed border-slate-300"}`}
      aria-label={`${rule.hazard_name} on camera ${rule.camera_id}`}>
      <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{rule.hazard_name}</h3>
          <p className="text-xs text-slate-500">{rule.hazard_id} · checked in the {ZONE_TYPE_LABEL[needs].toLowerCase()}</p>
        </div>
        {canEdit ? (
          <label className="flex shrink-0 items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" className="h-4 w-4 accent-brand-600" checked={v.enabled}
              onChange={(e) => set("enabled", e.target.checked)} />
            Checking on
          </label>
        ) : (
          <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${v.enabled
            ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-600 ring-slate-200"}`}>
            {v.enabled ? "Checking on" : "Turned off"}
          </span>
        )}
      </header>

      <div className="flex-1 space-y-4 px-5 py-4">
        <p className="text-sm text-slate-600">{v.enabled ? summary(v) : "This rule is turned off: the camera system does not check this hazard on this camera."}</p>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
          {FIELDS.map(({ key, label, hint }) => {
            const id = `r${rule.rule_id}-${key}`;
            return (
              <div key={key}>
                <dt>
                  <label htmlFor={id} className="field-label" title={hint}>{label}</label>
                </dt>
                <dd>
                  {canEdit ? (
                    <div className="relative">
                      <input id={id} inputMode="decimal" className={`field pr-9 ${errors[key] ? "field-invalid" : ""}`}
                        value={v[key]} onChange={(e) => set(key, e.target.value)} aria-invalid={!!errors[key]}
                        title={hint} />
                      <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-slate-500">
                        {RANGES[key].unit}
                      </span>
                    </div>
                  ) : (
                    <p className="text-sm font-medium text-slate-900">
                      {RANGES[key].unit === "%" ? formatPercent(Number(v[key]) / 100) : formatSeconds(Number(v[key]))}
                    </p>
                  )}
                  {errors[key] && <p className="field-error">{errors[key]}</p>}
                </dd>
              </div>
            );
          })}
          <div>
            <dt><label htmlFor={`r${rule.rule_id}-sev`} className="field-label" title={SEVERITY_HINT}>Severity</label></dt>
            <dd>
              {canEdit ? (
                <select id={`r${rule.rule_id}-sev`} className="field" value={v.severity}
                  onChange={(e) => set("severity", e.target.value as Severity)}>
                  {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              ) : <SeverityBadge severity={v.severity} />}
            </dd>
          </div>
        </dl>

        <fieldset>
          <legend className="field-label">Areas where it is checked</legend>
          {usable.length === 0 ? (
            <p className="text-sm text-slate-500">No {ZONE_TYPE_LABEL[needs].toLowerCase()} on this camera yet.</p>
          ) : (
            <ul className="mt-1 flex flex-wrap gap-2">
              {usable.map((z) => {
                const on = v.zoneIds.includes(z.zone_id);
                return (
                  <li key={z.zone_id}>
                    {canEdit ? (
                      <label className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm ${
                        on ? "border-brand-300 bg-brand-50 text-slate-900" : "border-slate-200 text-slate-600"}`}>
                        <input type="checkbox" className="accent-brand-600" checked={on}
                          onChange={() => set("zoneIds", on ? v.zoneIds.filter((x) => x !== z.zone_id) : [...v.zoneIds, z.zone_id])} />
                        <span className={`h-2.5 w-2.5 rounded-sm ${ZONE_STYLE[z.type].swatch}`} />
                        {z.name}{z.active ? "" : " (inactive)"}
                      </label>
                    ) : on ? (
                      <span className="flex items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm text-slate-700">
                        <span className={`h-2.5 w-2.5 rounded-sm ${ZONE_STYLE[z.type].swatch}`} />
                        {z.name}{z.active ? "" : " (inactive)"}
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
          {errors.zoneIds && <p className="field-error">{errors.zoneIds}</p>}
        </fieldset>

        <NoticeBar notice={notice} onClose={() => setNotice(null)} />
      </div>

      {canEdit && (
        <footer className="flex items-center justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-3">
          {dirty && !saving && <span className="mr-auto text-xs text-amber-700">Unsaved changes</span>}
          <button type="button" className="btn-secondary" disabled={!dirty || saving}
            onClick={() => { setV(formFromRule(rule)); setErrors({}); setNotice(null); }}>
            Reset
          </button>
          <button type="button" className="btn-primary" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save"}
          </button>
        </footer>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- hazard not set up on a camera

function MissingRule({ hazard, camera, zones, canEdit, onCreated }:
  { hazard: Hazard; camera: Camera; zones: Zone[]; canEdit: boolean; onCreated: (n: Notice) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needs = zoneTypeFor(hazard.hazard_id);
  const usable = zones.filter((z) => z.type === needs && z.active);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const { headers } = await apiWithHeaders<Rule>("/api/rules", { method: "POST",
        body: { hazard_id: hazard.hazard_id, camera_id: camera.camera_id, zone_ids: usable.map((z) => z.zone_id) } });
      const reload = workerReload(headers);
      onCreated({ tone: reload === "failed" ? "warn" : "ok", text: `${hazard.name} is now checked on ${camera.name}. ${SAVED_MESSAGE[reload]}` });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The rule could not be added.");
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 self-start rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-5">
      <div>
        <h3 className="text-sm font-semibold text-slate-700">{hazard.name}</h3>
        <p className="mt-1 text-sm text-slate-500">
          Not checked on this camera.
          {usable.length === 0 && <> It needs a {ZONE_TYPE_LABEL[needs].toLowerCase()}, which this camera does not have.</>}
        </p>
        {error && <p className="field-error">{error}</p>}
      </div>
      {canEdit && (usable.length > 0 ? (
        <button type="button" className="btn-secondary self-start" onClick={create} disabled={busy}>
          {busy ? "Adding…" : "Add rule with default values"}
        </button>
      ) : (
        <Link to={`/cameras/${camera.camera_id}`} className="self-start text-sm font-medium text-brand-700 hover:underline">
          Draw a {ZONE_TYPE_LABEL[needs].toLowerCase()} on {camera.name}
        </Link>
      ))}
    </section>
  );
}

// ---------------------------------------------------------------- page

export default function RulesPage() {
  const { hasRole } = useAuth();
  const canEdit = hasRole("Admin", "Technical");
  const cameras = useApi<Camera[]>("/api/cameras");
  const hazards = useApi<Hazard[]>("/api/hazards");
  const rules = useApi<Rule[]>("/api/rules");
  const zones = useApi<Zone[]>("/api/zones");
  const [notice, setNotice] = useState<Notice>(null);

  const error = cameras.error ?? hazards.error ?? rules.error ?? zones.error;
  const ready = cameras.data && hazards.data && rules.data && zones.data;

  return (
    <div className="space-y-6">
      <Card>
        <div className="space-y-4 p-5 text-sm text-slate-600">
          <p>
            Each hazard has one rule per camera. A rule decides when what the camera sees becomes an incident: how sure the
            system must be, for how long, and in which areas.
            {canEdit ? " Changes are sent to the camera system as soon as you save."
              : " Only administrators and technical staff can change rules; this page is read-only for you."}
          </p>
          <details className="group rounded-lg bg-slate-50 px-4 py-3" open>
            <summary className="cursor-pointer text-sm font-medium text-slate-800">What the values mean</summary>
            <dl className="mt-3 grid gap-x-8 gap-y-2 md:grid-cols-2">
              {[...FIELDS.map(({ label, hint }) => [label, hint]), ["Severity", SEVERITY_HINT]].map(([label, hint]) => (
                <div key={label}>
                  <dt className="inline font-medium text-slate-800">{label}: </dt>
                  <dd className="inline">{hint}</dd>
                </div>
              ))}
            </dl>
          </details>
        </div>
      </Card>
      <NoticeBar notice={notice} onClose={() => setNotice(null)} />

      {error ? <Card><ErrorState message={error} onRetry={() => { cameras.reload(); hazards.reload(); rules.reload(); zones.reload(); }} /></Card>
        : !ready ? <Card><Spinner /></Card>
        : cameras.data!.length === 0 ? <Card><EmptyState title="No cameras yet" /></Card>
        : cameras.data!.map((cam) => {
          const camZones = zones.data!.filter((z) => z.camera_id === cam.camera_id);
          return (
            <section key={cam.camera_id} aria-labelledby={`cam-${cam.camera_id}`} className="space-y-3">
              <h2 id={`cam-${cam.camera_id}`} className="text-base font-semibold text-slate-900">
                {cam.name}
                {!cam.enabled && <span className="ml-2 text-sm font-normal text-slate-500">(camera turned off)</span>}
              </h2>
              <div className="grid gap-4 xl:grid-cols-2">
                {hazards.data!.map((h) => {
                  const rule = rules.data!.find((r) => r.camera_id === cam.camera_id && r.hazard_id === h.hazard_id);
                  return rule ? (
                    <RuleCard key={rule.rule_id} rule={rule} zones={camZones} canEdit={canEdit}
                      onSaved={(r) => rules.mutate(rules.data!.map((x) => (x.rule_id === r.rule_id ? r : x)))} />
                  ) : (
                    <MissingRule key={h.hazard_id} hazard={h} camera={cam} zones={camZones} canEdit={canEdit}
                      onCreated={(n) => { setNotice(n); rules.reload(); }} />
                  );
                })}
              </div>
            </section>
          );
        })}
    </div>
  );
}
