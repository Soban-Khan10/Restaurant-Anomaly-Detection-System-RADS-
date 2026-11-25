import { useState } from "react";
import { CheckIcon, DownloadIcon } from "../components/Icons";
import { Card, ChoicePill, EmptyState, ErrorState, Spinner } from "../components/ui";
import { ApiError, api, apiBlob, saveBlob } from "../lib/api";
import { daysInclusive, formatDateTime, formatDay, pakistanDate, pakistanDateDaysAgo } from "../lib/format";
import type { Report, ReportFormat } from "../lib/types";
import { useApi } from "../lib/useApi";

type PeriodChoice = "today" | "week" | "custom";
const MAX_DAYS = 366; // same limit as the server

const FORMATS: { value: ReportFormat; label: string; hint: string }[] = [
  { value: "pdf", label: "PDF", hint: "For reading, sharing and printing" },
  { value: "xlsx", label: "Excel", hint: "For sorting and your own calculations" },
];
const FORMAT_LABEL: Record<ReportFormat, string> = { pdf: "PDF", xlsx: "Excel" };

function periodText(start: string, end: string): string {
  return start === end ? formatDay(start) : `${formatDay(start)} – ${formatDay(end)}`;
}

/** Downloads a report with the login token and hands it to the browser's save. */
function useDownload() {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<{ id: number; message: string } | null>(null);
  async function download(r: Report) {
    setBusy(r.report_id);
    setError(null);
    try {
      const { blob, fileName } = await apiBlob(r.download_url);
      saveBlob(blob, fileName ?? r.file_name);
    } catch (e) {
      setError({ id: r.report_id, message: e instanceof ApiError ? e.message : "The download failed." });
    } finally {
      setBusy(null);
    }
  }
  return { busy, error, download };
}

export default function ReportsPage() {
  const reports = useApi<Report[]>("/api/reports");
  const today = pakistanDate();
  const [choice, setChoice] = useState<PeriodChoice>("week");
  const [from, setFrom] = useState(() => pakistanDateDaysAgo(6));
  const [to, setTo] = useState(today);
  const [format, setFormat] = useState<ReportFormat>("pdf");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Report | null>(null);
  const { busy, error: downloadError, download } = useDownload();

  const [start, end] = choice === "today" ? [today, today]
    : choice === "week" ? [pakistanDateDaysAgo(6), today] : [from, to];

  let problem: string | null = null;
  if (choice === "custom") {
    if (!from || !to) problem = "Choose both a start and an end date.";
    else if (to < from) problem = "The end date must be on or after the start date.";
    else if (to > today) problem = "The end date cannot be in the future.";
    else if (daysInclusive(from, to) > MAX_DAYS) problem = `A report can cover at most ${MAX_DAYS} days.`;
  }

  async function generate() {
    if (problem) return;
    setCreating(true);
    setError(null);
    setCreated(null);
    try {
      const r = await api<Report>("/api/reports", { method: "POST",
        body: { period_start: start, period_end: end, format } });
      setCreated(r);
      reports.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The report could not be created. Please try again.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card title="Create a report">
        <div className="space-y-5 p-5">
          <fieldset>
            <legend className="field-label">Period</legend>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChoicePill label="Today" active={choice === "today"} onClick={() => setChoice("today")} />
              <ChoicePill label="Last 7 days" active={choice === "week"} onClick={() => setChoice("week")} />
              <ChoicePill label="Custom" active={choice === "custom"} onClick={() => setChoice("custom")} />
            </div>
            {choice === "custom" && (
              <div className="mt-3 grid max-w-md grid-cols-2 gap-3">
                <div>
                  <label htmlFor="r-from" className="field-label">From</label>
                  <input id="r-from" type="date" className="field" value={from} max={to || today}
                    onChange={(e) => setFrom(e.target.value)} />
                </div>
                <div>
                  <label htmlFor="r-to" className="field-label">To</label>
                  <input id="r-to" type="date" className="field" value={to} min={from || undefined} max={today}
                    onChange={(e) => setTo(e.target.value)} />
                </div>
              </div>
            )}
            <p className={`mt-2 text-sm ${problem ? "text-red-700" : "text-slate-600"}`}>
              {problem ?? <>Covers <span className="font-medium text-slate-900">{periodText(start, end)}</span>
                {" "}({daysInclusive(start, end)} {daysInclusive(start, end) === 1 ? "day" : "days"}, Pakistan time)</>}
            </p>
          </fieldset>

          <fieldset>
            <legend className="field-label">File type</legend>
            <div className="mt-1 grid max-w-xl gap-2 sm:grid-cols-2">
              {FORMATS.map((f) => (
                <label key={f.value} className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3.5 py-3
                  transition ${format === f.value ? "border-brand-400 bg-brand-50 ring-2 ring-brand-100"
                  : "border-slate-200 hover:bg-slate-50"}`}>
                  <input type="radio" name="format" value={f.value} checked={format === f.value}
                    onChange={() => setFormat(f.value)} className="mt-0.5 h-4 w-4 accent-brand-600" />
                  <span>
                    <span className="block text-sm font-medium text-slate-900">{f.label}</span>
                    <span className="block text-xs text-slate-500">{f.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className="btn-primary" onClick={generate} disabled={creating || !!problem}
              aria-busy={creating}>
              {creating && (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                  aria-hidden="true" />
              )}
              {creating ? "Creating report…" : "Generate report"}
            </button>
            {creating && <span className="text-sm text-slate-500">This can take a few seconds.</span>}
          </div>

          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</p>}
          {created && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-200
              bg-emerald-50 px-4 py-3 text-sm text-emerald-800" role="status">
              <span className="flex items-center gap-2">
                <CheckIcon className="h-4 w-4 shrink-0" />
                Report ready: {periodText(created.period_start, created.period_end)} ({FORMAT_LABEL[created.format]})
              </span>
              <button type="button" className="btn-secondary" onClick={() => download(created)}
                disabled={busy === created.report_id}>
                <DownloadIcon className="h-4 w-4" />
                {busy === created.report_id ? "Downloading…" : "Download"}
              </button>
            </div>
          )}
        </div>
      </Card>

      <Card title="Past reports">
        {reports.error ? <ErrorState message={reports.error} onRetry={reports.reload} />
          : !reports.data ? <Spinner />
          : reports.data.length === 0 ? (
            <EmptyState title="No reports yet" hint="Reports you create appear here for everyone to download." />
          ) : (
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[44rem] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs font-medium tracking-wide text-slate-500
                  uppercase">
                  <tr>
                    <th scope="col" className="px-5 py-3">Period</th>
                    <th scope="col" className="px-4 py-3">Type</th>
                    <th scope="col" className="px-4 py-3">Created</th>
                    <th scope="col" className="px-4 py-3">Created by</th>
                    <th scope="col" className="px-5 py-3 text-right"><span className="sr-only">Download</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {reports.data.map((r) => (
                    <tr key={r.report_id} className={created?.report_id === r.report_id ? "bg-emerald-50/50" : ""}>
                      <td className="px-5 py-3 font-medium text-slate-900">{periodText(r.period_start, r.period_end)}</td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
                          r.format === "pdf" ? "bg-red-50 text-red-700 ring-red-200"
                            : "bg-emerald-50 text-emerald-700 ring-emerald-200"}`}>
                          {FORMAT_LABEL[r.format]}
                        </span>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-slate-600">{formatDateTime(r.created_at)}</td>
                      <td className="px-4 py-3 text-slate-700">{r.created_by_name}</td>
                      <td className="px-5 py-3 text-right">
                        {r.available ? (
                          <button type="button" className="btn-secondary !py-1 text-xs" onClick={() => download(r)}
                            disabled={busy === r.report_id}
                            aria-label={`Download ${FORMAT_LABEL[r.format]} report for ${periodText(r.period_start,
                              r.period_end)}`}>
                            <DownloadIcon className="h-3.5 w-3.5" />
                            {busy === r.report_id ? "Downloading…" : "Download"}
                          </button>
                        ) : (
                          <span className="text-xs text-slate-500">File no longer available</span>
                        )}
                        {downloadError?.id === r.report_id && (
                          <p className="mt-1 text-xs text-red-700" role="alert">{downloadError.message}</p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
    </div>
  );
}
