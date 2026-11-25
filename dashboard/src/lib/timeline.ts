// Timeline of an incident for the detail page: detected -> (escalated) -> acknowledged -> reviewed -> ended,
// with the steps still waiting for someone shown at the end.
import { STATUS_LABEL, formatDuration } from "./format";
import type { IncidentDetail, ReviewEvent } from "./types";

export type Tone = "start" | "warn" | "done" | "confirmed" | "false" | "end" | "pending";
export interface Step {
  key: string;
  at: string | null;
  title: string;
  detail?: string;
  note?: string | null;
  tone: Tone;
}

function reviewStep(e: ReviewEvent, n: number): Step {
  const changed = e.old_status === "Confirmed violation" || e.old_status === "False alarm";
  const title = changed ? `Review changed to "${STATUS_LABEL[e.new_status]}"`
    : e.new_status === "Acknowledged" ? "Acknowledged"
    : e.new_status === "Confirmed violation" ? "Confirmed as a violation"
    : "Marked as a false alarm";
  const tone: Tone = e.new_status === "Confirmed violation" ? "confirmed"
    : e.new_status === "False alarm" ? "false" : "done";
  return { key: `review-${n}`, at: e.at, title, detail: `by ${e.user_name ?? "an unknown user"}`, note: e.note, tone };
}

export function timelineSteps(i: IncidentDetail): Step[] {
  const steps: Step[] = [
    { key: "start", at: i.start_time, title: "Detected", detail: `${i.camera_name} · ${i.zone_name}`, tone: "start" },
  ];
  if (i.escalated_at) {
    steps.push({ key: "escalated", at: i.escalated_at, title: "Escalated to administrators",
      detail: "Not reviewed within 10 minutes", tone: "warn" });
  }
  i.history.forEach((e, n) => steps.push(reviewStep(e, n)));
  if (i.end_time) {
    steps.push({ key: "end", at: i.end_time, title: "No longer seen by the camera",
      detail: `Lasted ${formatDuration(i.start_time, i.end_time)}`, tone: "end" });
  }
  steps.sort((a, b) => Date.parse(a.at!) - Date.parse(b.at!)); // stable: equal times keep their order

  const acknowledged = i.history.some((e) => e.new_status === "Acknowledged") || i.status !== "New";
  if (!acknowledged) {
    steps.push({ key: "wait-ack", at: null, title: "Acknowledge", detail: "Waiting for someone to look at it",
      tone: "pending" });
  }
  if (i.status === "New" || i.status === "Acknowledged") {
    steps.push({ key: "wait-review", at: null, title: "Review",
      detail: "Waiting: confirm the violation or mark it as a false alarm", tone: "pending" });
  }
  return steps;
}
