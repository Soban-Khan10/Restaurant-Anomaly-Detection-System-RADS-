import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ChevronLeftIcon, ChevronRightIcon } from "../components/Icons";
import { Card, EmptyState, ErrorState, OngoingBadge, SeverityBadge, Spinner, StatusBadge } from "../components/ui";
import {
  SEVERITIES, STATUS_LABEL, STATUS_ORDER, formatDateTime, formatDuration, pakistanDate, pakistanDateDaysAgo,
} from "../lib/format";
import type { Camera, Hazard, IncidentPage } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useIncidentEvents } from "../live/LiveContext";

const FILTER_KEYS = ["date_from", "date_to", "camera_id", "hazard_id", "severity", "status"] as const;
const PAGE_SIZES = [25, 50, 100];

function QuickRange({ label, from, to, active, onPick }:
  { label: string; from: string; to: string; active: boolean; onPick: (from: string, to: string) => void }) {
  return (
    <button type="button" onClick={() => onPick(from, to)} aria-pressed={active}
      className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition ${
        active ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-600 ring-slate-300 hover:bg-slate-50"}`}>
      {label}
    </button>
  );
}

export default function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [newArrived, setNewArrived] = useState(false);

  const page = Math.max(1, Number(params.get("page")) || 1);
  const pageSize = PAGE_SIZES.includes(Number(params.get("page_size"))) ? Number(params.get("page_size")) : 25;
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as
    Record<(typeof FILTER_KEYS)[number], string>;

  const cameras = useApi<Camera[]>("/api/cameras");
  const hazards = useApi<Hazard[]>("/api/hazards");
  const result = useApi<IncidentPage>("/api/incidents", { ...filters, page, page_size: pageSize });
  const { reload } = result;

  // Live: on the first page, refresh when incidents open or close; elsewhere, offer to jump to the newest.
  useIncidentEvents(() => (page === 1 ? reload() : setNewArrived(true)));

  function update(patch: Record<string, string | number | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, String(v));
    }
    if (!("page" in patch)) next.delete("page"); // a new filter starts at the first page
    if (next.get("page") === "1") next.delete("page");
    setParams(next);
    setNewArrived(false);
  }

  const today = pakistanDate();
  const ranges = [
    { label: "Today", from: today, to: today },
    { label: "Last 7 days", from: pakistanDateDaysAgo(6), to: today },
    { label: "Last 30 days", from: pakistanDateDaysAgo(29), to: today },
  ];
  const anyFilter = FILTER_KEYS.some((k) => filters[k]);
  const data = result.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const first = data && data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const last = data ? Math.min(data.page * data.page_size, data.total) : 0;

  return (
    <div className="space-y-4">
      <Card>
        <div className="space-y-4 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-medium text-slate-600">Period:</span>
            {ranges.map((r) => (
              <QuickRange key={r.label} {...r} active={filters.date_from === r.from && filters.date_to === r.to}
                onPick={(from, to) => update({ date_from: from, date_to: to })} />
            ))}
            <QuickRange label="All time" from="" to="" active={!filters.date_from && !filters.date_to}
              onPick={() => update({ date_from: null, date_to: null })} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
            <div>
              <label htmlFor="f-from" className="field-label">From</label>
              <input id="f-from" type="date" className="field" value={filters.date_from} max={filters.date_to || undefined}
                onChange={(e) => update({ date_from: e.target.value })} />
            </div>
            <div>
              <label htmlFor="f-to" className="field-label">To</label>
              <input id="f-to" type="date" className="field" value={filters.date_to} min={filters.date_from || undefined}
                onChange={(e) => update({ date_to: e.target.value })} />
            </div>
            <div>
              <label htmlFor="f-camera" className="field-label">Camera</label>
              <select id="f-camera" className="field" value={filters.camera_id}
                onChange={(e) => update({ camera_id: e.target.value })}>
                <option value="">All cameras</option>
                {cameras.data?.map((c) => <option key={c.camera_id} value={c.camera_id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="f-hazard" className="field-label">Hazard</label>
              <select id="f-hazard" className="field" value={filters.hazard_id}
                onChange={(e) => update({ hazard_id: e.target.value })}>
                <option value="">All hazards</option>
                {hazards.data?.map((h) => <option key={h.hazard_id} value={h.hazard_id}>{h.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="f-severity" className="field-label">Severity</label>
              <select id="f-severity" className="field" value={filters.severity}
                onChange={(e) => update({ severity: e.target.value })}>
                <option value="">Any severity</option>
                {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="f-status" className="field-label">Review status</label>
              <select id="f-status" className="field" value={filters.status}
                onChange={(e) => update({ status: e.target.value })}>
                <option value="">Any status</option>
                {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
              </select>
            </div>
          </div>
          {anyFilter && (
            <button type="button" className="text-sm font-medium text-brand-700 hover:underline"
              onClick={() => update(Object.fromEntries(FILTER_KEYS.map((k) => [k, null])))}>
              Clear all filters
            </button>
          )}
        </div>
      </Card>

      {newArrived && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-brand-200 bg-brand-50
          px-4 py-3 text-sm text-brand-900">
          New incidents have come in since this page was loaded.
          <button type="button" className="btn-secondary" onClick={() => update({ page: null })}>Show newest</button>
        </div>
      )}

      <Card>
        {result.error ? <ErrorState message={result.error} onRetry={reload} />
          : !data ? <Spinner />
          : data.items.length === 0 ? (
            <EmptyState title={anyFilter ? "No incidents match these filters" : "No incidents recorded yet"}
              hint={anyFilter ? "Try a longer period or clear some filters." : undefined} />
          ) : (
            <div className={`overflow-x-auto transition-opacity ${result.loading ? "opacity-60" : ""}`}>
              <table className="w-full min-w-[56rem] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs font-medium uppercase tracking-wide
                  text-slate-500">
                  <tr>
                    <th scope="col" className="px-5 py-3">When</th>
                    <th scope="col" className="px-4 py-3">Hazard</th>
                    <th scope="col" className="px-4 py-3">Camera</th>
                    <th scope="col" className="px-4 py-3">Area</th>
                    <th scope="col" className="px-4 py-3">Severity</th>
                    <th scope="col" className="px-4 py-3">Review status</th>
                    <th scope="col" className="px-5 py-3">How long</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((i) => (
                    <tr key={i.incident_id} className="cursor-pointer hover:bg-slate-50"
                      onClick={() => navigate(`/incidents/${i.incident_id}`)}>
                      <td className="whitespace-nowrap px-5 py-3 text-slate-700">
                        <Link to={`/incidents/${i.incident_id}`} className="hover:underline"
                          onClick={(e) => e.stopPropagation()}>
                          {formatDateTime(i.start_time)}
                        </Link>
                      </td>
                      <td className="px-4 py-3 font-medium text-slate-900">{i.hazard_name}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-700">{i.camera_name}</td>
                      <td className="px-4 py-3 text-slate-700">{i.zone_name}</td>
                      <td className="px-4 py-3"><SeverityBadge severity={i.severity} /></td>
                      <td className="px-4 py-3"><StatusBadge status={i.status} /></td>
                      <td className="whitespace-nowrap px-5 py-3 text-slate-600">
                        {i.end_time ? formatDuration(i.start_time, i.end_time) : <OngoingBadge />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

        {data && data.total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-3 text-sm
            text-slate-600">
            <p>
              Showing <span className="font-medium text-slate-900">{first}–{last}</span> of{" "}
              <span className="font-medium text-slate-900">{data.total}</span> incidents
            </p>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2">
                <span className="text-xs">Per page</span>
                <select className="field !w-auto !py-1" value={pageSize}
                  onChange={(e) => update({ page_size: e.target.value === "25" ? null : e.target.value })}>
                  {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <span className="tabular-nums">Page {page} of {pages}</span>
              <div className="flex gap-1">
                <button type="button" className="btn-secondary !px-2" disabled={page <= 1} aria-label="Previous page"
                  onClick={() => update({ page: page - 1 })}>
                  <ChevronLeftIcon className="h-4 w-4" />
                </button>
                <button type="button" className="btn-secondary !px-2" disabled={page >= pages} aria-label="Next page"
                  onClick={() => update({ page: page + 1 })}>
                  <ChevronRightIcon className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
