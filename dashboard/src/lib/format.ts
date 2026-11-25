// Plain-language labels and Pakistan-time formatting. All times are shown in the kitchen's time zone,
// whatever time zone the viewing computer uses.

import type { CameraStatus, IncidentStatus, Role, Severity } from "./types";

export const TIME_ZONE = "Asia/Karachi";

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  New: "Not reviewed",
  Acknowledged: "Acknowledged",
  "Confirmed violation": "Confirmed",
  "False alarm": "False alarm",
};

export const STATUS_ORDER: IncidentStatus[] = ["New", "Acknowledged", "Confirmed violation", "False alarm"];
export const SEVERITIES: Severity[] = ["High", "Medium", "Low"];

export const CAMERA_STATUS_LABEL: Record<CameraStatus, string> = {
  online: "Online",
  offline: "Offline",
  reconnecting: "Reconnecting",
};

export const ROLE_LABEL: Record<Role, string> = {
  Admin: "Administrator",
  Manager: "Manager",
  Technical: "Technical staff",
};

const dateTimeFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  hour12: false,
});
const timeFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit",
  hour12: false });
const isoDateFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE, year: "numeric", month: "2-digit",
  day: "2-digit" });

/** "24 Nov 2026, 14:05" */
export function formatDateTime(iso: string): string {
  return dateTimeFmt.format(new Date(iso));
}

const dateTimeSecondsFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
});

/** "24 Nov 2026, 14:05:31" */
export function formatDateTimeSeconds(iso: string): string {
  return dateTimeSecondsFmt.format(new Date(iso));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A calendar day "2026-11-24" -> "24 Nov 2026" (no time zone involved). */
export function formatDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return y && m && d ? `${d} ${MONTHS[m - 1]} ${y}` : day;
}

/** Number of days from `from` to `to`, both included ("2026-11-18".."2026-11-24" -> 7). */
export function daysInclusive(from: string, to: string): number {
  return Math.round((Date.parse(to + "T00:00:00Z") - Date.parse(from + "T00:00:00Z")) / 86_400_000) + 1;
}

/** 0.784 -> "78%" */
export function formatPercent(fraction: number | null | undefined): string {
  return fraction == null ? "—" : `${Math.round(fraction * 100)}%`;
}

/** Rule durations: 5 -> "5 s", 1.5 -> "1.5 s", 300 -> "5 min", 90 -> "1 min 30 s". */
export function formatSeconds(seconds: number): string {
  const s = Math.round(seconds * 10) / 10;
  if (s < 60) return `${s} s`;
  const min = Math.floor(s / 60);
  const rest = Math.round((s - min * 60) * 10) / 10;
  return rest ? `${min} min ${rest} s` : `${min} min`;
}

/** "14:05" */
export function formatTime(iso: string): string {
  return timeFmt.format(new Date(iso));
}

/** YYYY-MM-DD of `date` in Pakistan time (what the server's date filters expect). */
export function pakistanDate(date: Date = new Date()): string {
  return isoDateFmt.format(date);
}

/** YYYY-MM-DD, `days` days before today in Pakistan time. */
export function pakistanDateDaysAgo(days: number, now: Date = new Date()): string {
  return pakistanDate(new Date(now.getTime() - days * 86_400_000));
}

/** "Today", "3 min ago", "2 h ago", or the date for older times. */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 6 * 3600) return `${Math.floor(seconds / 3600)} h ago`;
  return formatDateTime(iso);
}

/** How long an incident lasted: "45 s", "3 min 10 s", "1 h 05 min". */
export function formatDuration(startIso: string, endIso: string): string {
  const s = Math.max(0, Math.round((new Date(endIso).getTime() - new Date(startIso).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}
