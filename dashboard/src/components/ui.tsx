// Small shared building blocks: cards, badges, states.
import type { ReactNode } from "react";
import { CAMERA_STATUS_LABEL, STATUS_LABEL } from "../lib/format";
import type { CameraStatus, IncidentStatus, Severity } from "../lib/types";
import { InfoIcon } from "./Icons";

export function Card({ title, action, children, className = "" }:
  { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const badgeBase = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium";

const severityClass: Record<Severity, string> = {
  High: "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  Medium: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200",
  Low: "bg-slate-100 text-slate-700 ring-1 ring-inset ring-slate-200",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={`${badgeBase} ${severityClass[severity]}`}>{severity}</span>;
}

const statusClass: Record<IncidentStatus, string> = {
  New: "bg-brand-50 text-brand-800 ring-1 ring-inset ring-brand-200",
  Acknowledged: "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-200",
  "Confirmed violation": "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  "False alarm": "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200",
};

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return <span className={`${badgeBase} ${statusClass[status]}`}>{STATUS_LABEL[status]}</span>;
}

const cameraDot: Record<CameraStatus, string> = {
  online: "bg-emerald-500",
  reconnecting: "bg-amber-500",
  offline: "bg-slate-400",
};
const cameraPill: Record<CameraStatus, string> = {
  online: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
  reconnecting: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200",
  offline: "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200",
};

export function CameraStatusBadge({ status }: { status: CameraStatus }) {
  return (
    <span className={`${badgeBase} ${cameraPill[status]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${cameraDot[status]} ${status === "online" ? "pulse-dot" : ""}`} />
      {CAMERA_STATUS_LABEL[status]}
    </span>
  );
}

export function OngoingBadge() {
  return (
    <span className={`${badgeBase} bg-red-600 text-white`}>
      <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-white" />
      Ongoing
    </span>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
      {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
      <p className="text-sm text-red-700">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary">Try again</button>
      )}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {hint && <p className="mt-1 text-sm text-slate-500">{hint}</p>}
    </div>
  );
}

/** The required reminder that the system's detections are decision support (SRS 4.2.3). */
export function ReviewNote({ compact = false }: { compact?: boolean }) {
  return (
    <div
      className={`flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 text-amber-900 ${
        compact ? "px-3 py-2 text-xs" : "px-4 py-3 text-sm"
      }`}
    >
      <InfoIcon className={`${compact ? "h-4 w-4" : "h-5 w-5"} mt-px shrink-0 text-amber-600`} />
      <p>
        <span className="font-semibold">Please check before acting.</span> Incidents are detected automatically
        by the camera system and can be wrong. A person must review every incident.
      </p>
    </div>
  );
}
