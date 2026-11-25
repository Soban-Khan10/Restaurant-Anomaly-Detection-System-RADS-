import { describe, expect, it } from "vitest";
import { timelineSteps } from "./timeline";
import type { IncidentDetail, ReviewEvent } from "./types";

function incident(over: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    incident_id: 1, hazard_id: "H-01", hazard_name: "Uncovered hair", rule_id: 1, camera_id: 1,
    camera_name: "Camera 1", zone_id: 1, zone_name: "Prep table", track_id: 7,
    start_time: "2026-11-24T10:00:00+05:00", end_time: null, conf_avg: 0.8, conf_max: 0.9, severity: "Medium",
    model_version: "test", status: "New", reviewed_by: null, reviewed_at: null, review_note: null,
    escalated_at: null, evidence_id: null,
    hazard: { hazard_id: "H-01", name: "Uncovered hair", tied_to: "worker", violation_class: "x", compliant_class: "y" },
    rule: { rule_id: 1, hazard_id: "H-01", camera_id: 1, enabled: true, conf_threshold: 0.45, window_s: 5,
      min_ratio: 0.7, end_gap_s: 5, cooldown_s: 60, severity: "Medium" },
    rule_changed_since: false, camera: { camera_id: 1, name: "Camera 1" },
    zone: { zone_id: 1, name: "Prep table", type: "prep" }, evidence: [], alerts: [], reviewed_by_user: null,
    history: [], ...over,
  };
}

const ev = (at: string, old_status: ReviewEvent["old_status"], new_status: ReviewEvent["new_status"],
  note: string | null = null): ReviewEvent => ({ at, user_id: 2, user_name: "Maria", old_status, new_status, note });

const titles = (i: IncidentDetail) => timelineSteps(i).map((s) => s.title);

describe("incident timeline", () => {
  it("a new, ongoing incident is waiting to be acknowledged and reviewed", () => {
    expect(titles(incident())).toEqual(["Detected", "Acknowledge", "Review"]);
    expect(timelineSteps(incident()).slice(1).every((s) => s.tone === "pending" && s.at === null)).toBe(true);
  });

  it("created, escalated, acknowledged, reviewed and ended, in time order", () => {
    const steps = timelineSteps(incident({
      status: "Confirmed violation",
      escalated_at: "2026-11-24T10:10:00+05:00",
      end_time: "2026-11-24T10:02:00+05:00",
      history: [ev("2026-11-24T10:12:00+05:00", "New", "Acknowledged"),
        ev("2026-11-24T10:20:00+05:00", "Acknowledged", "Confirmed violation", "hairnet off")],
    }));
    expect(steps.map((s) => s.title)).toEqual([
      "Detected", "No longer seen by the camera", "Escalated to administrators", "Acknowledged",
      "Confirmed as a violation"]);
    expect(steps[1].detail).toBe("Lasted 2 min 00 s");
    expect(steps[4]).toMatchObject({ detail: "by Maria", note: "hairnet off", tone: "confirmed" });
  });

  it("acknowledged only: the final review is still waiting", () => {
    expect(titles(incident({ status: "Acknowledged",
      history: [ev("2026-11-24T10:05:00+05:00", "New", "Acknowledged")] })))
      .toEqual(["Detected", "Acknowledged", "Review"]);
  });

  it("straight to false alarm skips the acknowledge step; a later change is labelled as such", () => {
    expect(titles(incident({ status: "Acknowledged", history: [
      ev("2026-11-24T10:05:00+05:00", "New", "False alarm"),
      ev("2026-11-24T10:06:00+05:00", "False alarm", "Acknowledged")] })))
      .toEqual(["Detected", "Marked as a false alarm", 'Review changed to "Acknowledged"', "Review"]);
  });
});
