// Pause monitoring: the pause dialog's choices, checks and plain-language summary, and the texts for tiles and the
// banner. Times people type are Pakistan wall-clock times (the kitchen's time zone), whatever the computer uses.
import { TIME_ZONE } from "./format";
import type { CameraMonitoring, Pause, PauseReason } from "./types";

export const PAUSE_REASONS: { value: PauseReason; label: string }[] = [
  { value: "cleaning", label: "Cleaning" },
  { value: "closed", label: "Restaurant closed / off day" },
  { value: "no_food_prep", label: "No food preparation" },
  { value: "maintenance", label: "Maintenance / technical work" },
  { value: "other", label: "Other" },
];

export const PAUSE_LENGTHS = [
  { value: "30", label: "30 min" },
  { value: "60", label: "1 hour" },
  { value: "120", label: "2 hours" },
  { value: "until", label: "Until a time" },
  { value: "resume", label: "Until I resume" },
] as const;

export type PauseLength = (typeof PAUSE_LENGTHS)[number]["value"];
const MAX_MS = 7 * 24 * 3600 * 1000;

export interface PauseFormValues {
  cameraId: number | null; // null = all cameras
  reason: PauseReason | "";
  note: string;
  when: "now" | "planned";
  startAt: string; // "YYYY-MM-DDTHH:mm", Pakistan time (a datetime-local input)
  length: PauseLength;
  untilAt: string; // same format
}

export type PauseFormErrors = Partial<Record<keyof PauseFormValues, string>>;

// ---------------------------------------------------------------- Pakistan wall-clock time <-> Date

const partsFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TIME_ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hourCycle: "h23",
});

/** Date -> "YYYY-MM-DDTHH:mm" in Pakistan time (the value a datetime-local input shows). */
export function toPkInput(d: Date): string {
  const p = Object.fromEntries(partsFmt.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

/** "YYYY-MM-DDTHH:mm" (Pakistan time) -> Date, or null if empty or invalid. */
export function fromPkInput(v: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v)) return null;
  const d = new Date(`${v}:00+05:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const dayFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, day: "numeric", month: "short" });
const hmFmt = new Intl.DateTimeFormat("en-GB", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit", hour12: false });
const pkDay = (d: Date) => toPkInput(d).slice(0, 10);

/** "16:00" if on the same Pakistan day as `ref`, else "26 Nov, 02:00". */
export function clock(d: Date, ref: Date): string {
  return pkDay(d) === pkDay(ref) ? hmFmt.format(d) : `${dayFmt.format(d)}, ${hmFmt.format(d)}`;
}

function dayWord(d: Date, now: Date): string {
  if (pkDay(d) === pkDay(now)) return "today";
  if (pkDay(d) === pkDay(new Date(now.getTime() + 86_400_000))) return "tomorrow";
  return `on ${dayFmt.format(d)}`;
}

// ---------------------------------------------------------------- the dialog

export function emptyPauseForm(cameraId: number | null, now: Date = new Date()): PauseFormValues {
  const inAnHour = new Date(Math.ceil((now.getTime() + 3600_000) / 900_000) * 900_000); // next quarter hour + 1 h
  return { cameraId, reason: "", note: "", when: "now", startAt: toPkInput(inAnHour), length: "60",
    untilAt: toPkInput(new Date(inAnHour.getTime() + 3600_000)) };
}

export function startOf(v: PauseFormValues, now: Date): Date | null {
  return v.when === "now" ? now : fromPkInput(v.startAt);
}

/** When the pause ends: null = until resumed (or not decidable yet). */
export function endOf(v: PauseFormValues, now: Date): Date | null {
  const start = startOf(v, now);
  if (!start) return null;
  if (v.length === "resume") return null;
  if (v.length === "until") return fromPkInput(v.untilAt);
  return new Date(start.getTime() + Number(v.length) * 60_000);
}

export function validatePauseForm(v: PauseFormValues, now: Date = new Date()): PauseFormErrors {
  const e: PauseFormErrors = {};
  if (!v.reason) e.reason = "Choose a reason.";
  if (v.reason === "other" && !v.note.trim()) e.note = "Write a short note when the reason is Other.";
  const start = startOf(v, now);
  if (v.when === "planned") {
    if (!start) e.startAt = "Choose the date and time the pause starts.";
    else if (start.getTime() <= now.getTime()) e.startAt = "A planned pause must start in the future.";
  }
  if (v.length === "until") {
    const end = fromPkInput(v.untilAt);
    if (!end) e.untilAt = "Choose the date and time the pause ends.";
    else if (start && end.getTime() <= start.getTime()) e.untilAt = "The end must be after the start.";
    else if (start && end.getTime() - start.getTime() > MAX_MS) e.untilAt = "A pause can last at most 7 days.";
  }
  return e;
}

/** Body for POST /api/pauses. */
export function pausePayload(v: PauseFormValues): Record<string, unknown> {
  const body: Record<string, unknown> = { camera_id: v.cameraId, reason: v.reason, note: v.note.trim() || null };
  if (v.when === "planned") body.starts_at = `${v.startAt}:00+05:00`;
  if (v.length === "until") body.ends_at = `${v.untilAt}:00+05:00`;
  else if (v.length !== "resume") body.duration_minutes = Number(v.length);
  return body;
}

/** "Detection on Camera 2 will stop now until 16:00. Live view keeps working. No incidents or alerts will be created." */
export function pauseSummary(v: PauseFormValues, cameraName: string | null, now: Date = new Date()): string {
  const subject = v.cameraId === null || cameraName === null ? "Detection on all cameras" : `Detection on ${cameraName}`;
  const start = startOf(v, now);
  const end = endOf(v, now);
  const startText = v.when === "now" || !start ? "now" : `${dayWord(start, now)} at ${hmFmt.format(start)}`;
  const endText = v.length === "resume" || !end ? "until you resume it" : `until ${clock(end, start ?? now)}`;
  return `${subject} will stop ${startText} ${endText}. Live view keeps working. No incidents or alerts will be created.`;
}

// ---------------------------------------------------------------- tiles and banner

/** "Cleaning – until 16:00" / "Cleaning – until resumed". */
export function pausedLine(p: Pause, now: Date = new Date()): string {
  return `${p.reason_label} – ${p.ends_at ? `until ${clock(new Date(p.ends_at), now)}` : "until resumed"}`;
}

/** "Cleaning, 18:00–19:00" for a planned pause. */
export function plannedLine(p: Pause, now: Date = new Date()): string {
  const start = new Date(p.starts_at);
  const day = pkDay(start) === pkDay(now) ? "" : `${dayFmt.format(start)}, `;
  return `${p.reason_label}, ${day}${hmFmt.format(start)}${p.ends_at ? `–${clock(new Date(p.ends_at), start)}` : " until resumed"}`;
}

/** The banner on every page, or null when nothing is paused. */
export function bannerText(status: CameraMonitoring[], now: Date = new Date()): string | null {
  const paused = status.filter((s) => s.state === "paused" && s.pause);
  if (paused.length === 0) return null;
  const until = (p: Pause) => (p.ends_at ? `until ${clock(new Date(p.ends_at), now)}` : "until resumed");
  const everywhere = paused.find((s) => s.pause!.camera_id === null)?.pause;
  if (everywhere && paused.length === status.length) {
    return `Monitoring paused on all cameras (${everywhere.reason_label}) ${until(everywhere)}`;
  }
  return "Monitoring paused on " + paused.map((s) => `${s.camera_name} (${s.pause!.reason_label}) ${until(s.pause!)}`)
    .join("; ");
}

/** The next moment the status changes by itself (a pause starts or ends), for an exact refresh. */
export function nextChange(status: CameraMonitoring[], now: Date = new Date()): Date | null {
  const times = status.flatMap((s) => [s.pause?.ends_at, s.next_pause?.starts_at])
    .filter((t): t is string => !!t).map((t) => new Date(t)).filter((d) => d.getTime() > now.getTime());
  return times.length ? new Date(Math.min(...times.map((d) => d.getTime()))) : null;
}
