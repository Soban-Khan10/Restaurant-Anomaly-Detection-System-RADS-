import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router";
import { useAuth } from "../auth/AuthContext";
import { EvidenceView } from "../components/EvidenceView";
import { ArrowLeftIcon, CheckIcon } from "../components/Icons";
import { Card, EmptyState, ErrorState, OngoingBadge, SeverityBadge, Spinner, StatusBadge } from "../components/ui";
import { ApiError, api } from "../lib/api";
import {
  formatDateTime, formatDateTimeSeconds, formatDuration, formatPercent, formatSeconds,
} from "../lib/format";
import { timelineSteps, type Tone } from "../lib/timeline";
import type { IncidentAlert, IncidentDetail, ReviewStatus } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useLive } from "../live/LiveContext";
import { NotFound } from "./Placeholders";

// Who may review: the same roles the server allows (app/auth.py ROLES_REVIEW).
const REVIEW_ROLES = ["Admin", "Manager"] as const;
const NOTE_MAX = 1000;

function useNow(ms: number, active: boolean): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(id);
  }, [ms, active]);
  return now;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="py-3 sm:grid sm:grid-cols-5 sm:gap-4">
      <dt className="text-sm text-slate-500 sm:col-span-2">
        {label}
        {hint && <span className="mt-0.5 block text-xs text-slate-400">{hint}</span>}
      </dt>
      <dd className="mt-1 text-sm text-slate-900 sm:col-span-3 sm:mt-0">{children}</dd>
    </div>
  );
}

// ---------------------------------------------------------------- what happened

function Details({ incident: i }: { incident: IncidentDetail }) {
  const ongoing = i.end_time === null;
  const now = useNow(5000, ongoing);
  const zoneHazard = i.hazard.tied_to === "zone";
  return (
    <dl className="divide-y divide-slate-100 px-5">
      <Field label="Hazard">{i.hazard_name} <span className="text-slate-400">({i.hazard_id})</span></Field>
      <Field label="Camera">{i.camera_name}</Field>
      <Field label="Area">{i.zone_name}</Field>
      <Field label="Worker ID" hint={zoneHazard ? "This hazard is about the area, not one person"
        : "Number the camera system gives each person it follows; not a staff number"}>
        {zoneHazard || i.track_id === null ? "—" : `#${i.track_id}`}
      </Field>
      <Field label="Confidence" hint="How sure the system was, across the pictures it checked">
        {i.conf_avg === null && i.conf_max === null ? "—" : (
          <>Average <span className="font-medium">{formatPercent(i.conf_avg)}</span>
            <span className="mx-2 text-slate-300">·</span>
            Highest <span className="font-medium">{formatPercent(i.conf_max)}</span></>
        )}
      </Field>
      <Field label="Severity"><SeverityBadge severity={i.severity} /></Field>
      <Field label="Started">{formatDateTimeSeconds(i.start_time)}</Field>
      <Field label="Ended">{ongoing ? <OngoingBadge /> : formatDateTimeSeconds(i.end_time!)}</Field>
      <Field label="Duration">
        {ongoing ? `${formatDuration(i.start_time, now.toISOString())} so far` : formatDuration(i.start_time, i.end_time!)}
      </Field>
    </dl>
  );
}

// ---------------------------------------------------------------- rule values

function RuleValuesCard({ incident: i }: { incident: IncidentDetail }) {
  const r = i.rule;
  return (
    <Card title="Why it was raised">
      <div className="space-y-3 px-5 pt-4">
        <p className="text-sm text-slate-600">
          The detection rule for this hazard on {i.camera_name} raises an incident when the hazard is seen in at
          least <strong className="font-medium text-slate-900">{formatPercent(r.min_ratio)}</strong> of the camera
          pictures over <strong className="font-medium text-slate-900">{formatSeconds(r.window_s)}</strong>, counting
          only detections the system is at least <strong className="font-medium text-slate-900">
          {formatPercent(r.conf_threshold)}</strong> sure of.
        </p>
        {i.rule_changed_since && (
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
            This rule has been changed since. The values below are the ones in force when the incident was detected.
          </p>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 px-5 py-4 sm:grid-cols-3">
        {[
          ["Minimum confidence", formatPercent(r.conf_threshold)],
          ["Time checked", formatSeconds(r.window_s)],
          ["Share of pictures needed", formatPercent(r.min_ratio)],
          ["Ends after not seen for", formatSeconds(r.end_gap_s)],
          ["Wait before alerting again", formatSeconds(r.cooldown_s)],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-slate-500">{label}</dt>
            <dd className="mt-0.5 text-sm font-medium text-slate-900">{value}</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-slate-500">Severity</dt>
          <dd className="mt-1"><SeverityBadge severity={r.severity} /></dd>
        </div>
      </dl>
    </Card>
  );
}

// ---------------------------------------------------------------- review

const ACTIONS: { status: ReviewStatus; label: string; hint: string; selected: string }[] = [
  { status: "Acknowledged", label: "Acknowledge", hint: "I have seen it and am checking",
    selected: "border-violet-400 bg-violet-50 ring-2 ring-violet-200" },
  { status: "Confirmed violation", label: "Confirm violation", hint: "It really happened",
    selected: "border-red-400 bg-red-50 ring-2 ring-red-200" },
  { status: "False alarm", label: "False alarm", hint: "The system got it wrong",
    selected: "border-slate-400 bg-slate-50 ring-2 ring-slate-200" },
];

const SAVED_TEXT: Record<ReviewStatus, string> = {
  Acknowledged: "Saved: incident acknowledged.",
  "Confirmed violation": "Saved: confirmed as a violation.",
  "False alarm": "Saved: marked as a false alarm.",
};

function ReviewCard({ incident: i, onSaved }: { incident: IncidentDetail; onSaved: (d: IncidentDetail) => void }) {
  const { hasRole } = useAuth();
  const canReview = hasRole(...REVIEW_ROLES);
  const [choice, setChoice] = useState<ReviewStatus | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const reviewer = i.reviewed_by_user ? i.reviewed_by_user.full_name || i.reviewed_by_user.username : null;

  async function save() {
    if (!choice) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api<IncidentDetail>(`/api/incidents/${i.incident_id}/review`, {
        method: "PATCH",
        body: { status: choice, ...(note.trim() ? { note: note.trim() } : {}) },
      });
      onSaved(updated);
      setSaved(SAVED_TEXT[choice]);
      setChoice(null);
      setNote("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The review could not be saved. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Review">
      <div className="space-y-4 p-5">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-slate-500">Current status</span>
            <StatusBadge status={i.status} />
          </div>
          {i.reviewed_at && (
            <p className="mt-1.5 text-xs text-slate-500">
              Last reviewed by {reviewer ?? "an unknown user"} on {formatDateTime(i.reviewed_at)}
            </p>
          )}
          {i.review_note && (
            <blockquote className="mt-2 rounded-lg border-l-4 border-slate-200 bg-slate-50 px-3 py-2 text-sm
              text-slate-700">
              {i.review_note}
            </blockquote>
          )}
        </div>

        {saved && (
          <p className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">
            <CheckIcon className="h-4 w-4 shrink-0" /> {saved}
          </p>
        )}

        {canReview ? (
          <div className="space-y-3">
            <p className="text-sm font-medium text-slate-800">
              {i.status === "New" ? "What is this?" : "Change the review"}
            </p>
            <div className="grid gap-2" role="group" aria-label="Review decision">
              {ACTIONS.map((a) => {
                const current = i.status === a.status;
                const picked = choice === a.status;
                return (
                  <button key={a.status} type="button" disabled={current || saving} aria-pressed={picked}
                    onClick={() => {
                      setChoice(picked ? null : a.status);
                      setSaved(null);
                      setError(null);
                    }}
                    className={`flex items-center justify-between gap-3 rounded-lg border px-3.5 py-2.5 text-left
                      transition disabled:cursor-not-allowed ${picked ? a.selected
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 disabled:bg-slate-50"}`}>
                    <span>
                      <span className={`block text-sm font-medium ${current ? "text-slate-400" : "text-slate-900"}`}>
                        {a.label}
                      </span>
                      <span className="block text-xs text-slate-500">{a.hint}</span>
                    </span>
                    {current && <span className="text-xs font-medium text-slate-400">Current</span>}
                  </button>
                );
              })}
            </div>

            {choice && (
              <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3.5">
                <div>
                  <label htmlFor="review-note" className="field-label">Note (optional)</label>
                  <textarea id="review-note" className="field min-h-20 resize-y" maxLength={NOTE_MAX} value={note}
                    autoFocus placeholder="For example, what you checked or what was done"
                    onChange={(e) => setNote(e.target.value)} />
                  <p className="mt-1 text-right text-xs text-slate-400">{note.length}/{NOTE_MAX}</p>
                </div>
                {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</p>}
                <div className="flex flex-wrap justify-end gap-2">
                  <button type="button" className="btn-secondary" disabled={saving}
                    onClick={() => { setChoice(null); setError(null); }}>
                    Cancel
                  </button>
                  <button type="button" className="btn-primary" disabled={saving} onClick={save}>
                    {saving ? "Saving…" : `Save: ${ACTIONS.find((a) => a.status === choice)!.label}`}
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Only managers and administrators can review incidents.</p>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- timeline

const DOT: Record<Tone, string> = {
  start: "bg-brand-600 ring-brand-100",
  warn: "bg-amber-500 ring-amber-100",
  done: "bg-violet-500 ring-violet-100",
  confirmed: "bg-red-600 ring-red-100",
  false: "bg-slate-500 ring-slate-200",
  end: "bg-slate-400 ring-slate-100",
  pending: "border-2 border-dashed border-slate-300 bg-white ring-white",
};

function Timeline({ incident }: { incident: IncidentDetail }) {
  const steps = timelineSteps(incident);
  return (
    <Card title="Timeline">
      <ol className="px-5 py-4">
        {steps.map((s, n) => (
          <li key={s.key} className="relative flex gap-3 pb-5 last:pb-0">
            {n < steps.length - 1 && (
              <span className="absolute top-4 bottom-0 left-[5px] w-px bg-slate-200" aria-hidden="true" />
            )}
            <span className={`relative mt-1 h-3 w-3 shrink-0 rounded-full ring-4 ${DOT[s.tone]}`} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className={`text-sm font-medium ${s.tone === "pending" ? "text-slate-500" : "text-slate-900"}`}>
                {s.title}
              </p>
              <p className="text-xs text-slate-500">
                {s.at ? `${formatDateTimeSeconds(s.at)}${s.detail ? " · " : ""}` : ""}{s.detail}
              </p>
              {s.note && <p className="mt-1 text-sm text-slate-700">"{s.note}"</p>}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

// ---------------------------------------------------------------- alerts

const CHANNEL_LABEL: Record<IncidentAlert["channel"], string> = {
  dashboard: "Dashboard",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  email: "Email",
};

function recipient(a: IncidentAlert): string {
  if (a.channel === "dashboard") return "everyone using the dashboard";
  if (a.channel === "telegram") return "the Telegram group";
  if (a.channel === "email" && a.user_id === null) return "nobody (no Manager or Admin has an email address)";
  return a.recipient_name ?? "a user";
}

function AlertsCard({ alerts }: { alerts: IncidentAlert[] }) {
  return (
    <Card title="Alerts sent">
      {alerts.length === 0 ? (
        <EmptyState title="No alerts were sent for this incident" />
      ) : (
        <ul className="divide-y divide-slate-100">
          {alerts.map((a) => (
            <li key={a.alert_id} className="flex items-start justify-between gap-3 px-5 py-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-900">
                  {CHANNEL_LABEL[a.channel]}
                  <span className="font-normal text-slate-500">
                    {" · "}{a.kind === "escalation" ? "Escalation" : "New incident alert"}
                  </span>
                </p>
                <p className="text-xs text-slate-500">To {recipient(a)} · {formatDateTimeSeconds(a.sent_at)}</p>
                {a.status === "failed" && a.error && (
                  <p className="mt-1 break-words text-xs text-red-700">{a.error}</p>
                )}
              </div>
              <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                a.status === "sent" ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                  : "bg-red-50 text-red-700 ring-red-200"}`}>
                {a.status === "sent" ? "Sent" : "Failed"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- page

export default function IncidentDetailPage() {
  const { id } = useParams();
  const location = useLocation();
  const back = (location.state as { back?: string } | null)?.back ?? "/incidents";
  const valid = !!id && /^\d+$/.test(id);
  const incident = useApi<IncidentDetail>(valid ? `/api/incidents/${id}` : null);
  const { reload } = incident;
  const { subscribe } = useLive();

  // The page stays current when the camera stops seeing the hazard.
  useEffect(() => subscribe((msg) => {
    if (msg.type === "incident.closed" && String(msg.incident_id) === id) reload();
  }), [subscribe, reload, id]);

  if (!valid) return <NotFound />;
  const d = incident.data && String(incident.data.incident_id) === id ? incident.data : null;

  const backLink = (
    <Link to={back} className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline">
      <ArrowLeftIcon className="h-4 w-4" /> Back to incidents
    </Link>
  );

  if (!d) {
    return (
      <div className="space-y-4">
        {backLink}
        <Card>
          {incident.error ? (
            incident.error === "Incident not found"
              ? <EmptyState title={`Incident #${id} was not found`} hint="It may have been typed wrongly." />
              : <ErrorState message={incident.error} onRetry={reload} />
          ) : <Spinner label="Loading incident…" />}
        </Card>
      </div>
    );
  }

  const snapshots = d.evidence;
  const label = `${d.hazard_name} · ${d.camera_name} · ${formatDateTime(d.start_time)}`;

  return (
    <div className="space-y-4">
      {backLink}

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 p-5">
          <div className="min-w-0">
            <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">Incident #{d.incident_id}</p>
            <h2 className="mt-1 text-xl font-semibold text-slate-900">{d.hazard_name}</h2>
            <p className="mt-1 text-sm text-slate-600">
              {d.camera_name} · {d.zone_name} · {formatDateTime(d.start_time)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={d.severity} />
            <StatusBadge status={d.status} />
            {d.end_time === null && <OngoingBadge />}
          </div>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="space-y-4 lg:col-span-3">
          <Card title="Evidence">
            <div className="space-y-4 p-5">
              {snapshots.length === 0
                ? <p className="text-sm text-slate-500">No picture was saved for this incident.</p>
                : snapshots.map((e) => <EvidenceView key={e.evidence_id} evidence={e} label={label} />)}
            </div>
          </Card>
          <Card title="What was detected"><Details incident={d} /></Card>
          <RuleValuesCard incident={d} />
        </div>
        <div className="space-y-4 lg:col-span-2">
          <ReviewCard incident={d} onSaved={incident.mutate} />
          <Timeline incident={d} />
          <AlertsCard alerts={d.alerts} />
        </div>
      </div>
    </div>
  );
}
