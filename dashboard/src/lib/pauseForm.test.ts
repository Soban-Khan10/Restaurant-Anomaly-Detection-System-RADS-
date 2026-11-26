import { describe, expect, it } from "vitest";
import {
  bannerText, emptyPauseForm, fromPkInput, nextChange, pausePayload, pauseSummary, pausedLine, plannedLine,
  toPkInput, validatePauseForm, type PauseFormValues,
} from "./pauseForm";
import type { CameraMonitoring, Pause } from "./types";

const NOW = new Date("2026-11-25T14:00:00+05:00"); // the browser's own time zone does not matter
const form = (over: Partial<PauseFormValues> = {}): PauseFormValues =>
  ({ ...emptyPauseForm(2, NOW), reason: "cleaning", ...over });

describe("pause dialog summary", () => {
  it("now, for two hours", () => {
    expect(pauseSummary(form({ length: "120" }), "Camera 2", NOW)).toBe(
      "Detection on Camera 2 will stop now until 16:00. Live view keeps working. No incidents or alerts will be created.");
  });

  it("all cameras, until resumed", () => {
    expect(pauseSummary(form({ cameraId: null, length: "resume" }), null, NOW)).toBe(
      "Detection on all cameras will stop now until you resume it. Live view keeps working. "
      + "No incidents or alerts will be created.");
  });

  it("planned for later today, until a time", () => {
    expect(pauseSummary(form({ when: "planned", startAt: "2026-11-25T18:00", length: "until",
      untilAt: "2026-11-25T19:30" }), "Camera 1", NOW))
      .toBe("Detection on Camera 1 will stop today at 18:00 until 19:30. Live view keeps working. "
        + "No incidents or alerts will be created.");
  });

  it("planned for tomorrow and past midnight", () => {
    expect(pauseSummary(form({ when: "planned", startAt: "2026-11-26T23:30", length: "60" }), "Camera 1", NOW))
      .toBe("Detection on Camera 1 will stop tomorrow at 23:30 until 27 Nov, 00:30. Live view keeps working. "
        + "No incidents or alerts will be created.");
  });
});

describe("pause dialog checks and request", () => {
  it("asks for a reason, and a note for Other", () => {
    expect(validatePauseForm(form({ reason: "" }), NOW)).toEqual({ reason: "Choose a reason." });
    expect(validatePauseForm(form({ reason: "other" }), NOW).note).toMatch(/note/);
    expect(validatePauseForm(form({ reason: "other", note: "Pest control" }), NOW)).toEqual({});
  });

  it("checks planned start and the end time", () => {
    expect(validatePauseForm(form({ when: "planned", startAt: "2026-11-25T13:00" }), NOW).startAt)
      .toBe("A planned pause must start in the future.");
    expect(validatePauseForm(form({ length: "until", untilAt: "2026-11-25T13:59" }), NOW).untilAt)
      .toBe("The end must be after the start.");
    expect(validatePauseForm(form({ length: "until", untilAt: "2026-12-03T14:00" }), NOW).untilAt)
      .toBe("A pause can last at most 7 days.");
  });

  it("sends Pakistan times with their offset", () => {
    expect(pausePayload(form({ length: "30" }))).toEqual({ camera_id: 2, reason: "cleaning", note: null,
      duration_minutes: 30 });
    expect(pausePayload(form({ cameraId: null, when: "planned", startAt: "2026-11-25T18:00", length: "until",
      untilAt: "2026-11-25T19:00", note: " deep clean " }))).toEqual({ camera_id: null, reason: "cleaning",
      note: "deep clean", starts_at: "2026-11-25T18:00:00+05:00", ends_at: "2026-11-25T19:00:00+05:00" });
    expect(pausePayload(form({ length: "resume" }))).not.toHaveProperty("ends_at");
    expect(toPkInput(NOW)).toBe("2026-11-25T14:00");
    expect(fromPkInput("2026-11-25T14:00")?.getTime()).toBe(NOW.getTime());
  });
});

const pause = (over: Partial<Pause> = {}): Pause => ({
  pause_id: 7, camera_id: 2, camera_name: "Camera 2", reason: "cleaning", reason_label: "Cleaning", note: null,
  starts_at: "2026-11-25T13:30:00+05:00", ends_at: "2026-11-25T16:00:00+05:00", created_by: 1,
  created_by_name: "Ahmad", created_at: "2026-11-25T13:30:00+05:00", ended_by: null, ended_by_name: null,
  ended_at: null, state: "active", ...over,
});
const cam = (id: number, over: Partial<CameraMonitoring> = {}): CameraMonitoring =>
  ({ camera_id: id, camera_name: `Camera ${id}`, enabled: true, state: "active", pause: null, next_pause: null, ...over });

describe("tile and banner texts", () => {
  it("describes paused and planned pauses", () => {
    expect(pausedLine(pause(), NOW)).toBe("Cleaning – until 16:00");
    expect(pausedLine(pause({ ends_at: null }), NOW)).toBe("Cleaning – until resumed");
    expect(plannedLine(pause({ starts_at: "2026-11-25T18:00:00+05:00", ends_at: "2026-11-25T19:00:00+05:00" }), NOW))
      .toBe("Cleaning, 18:00–19:00");
  });

  it("banner: none, one camera, all cameras", () => {
    expect(bannerText([cam(1), cam(2)], NOW)).toBeNull();
    expect(bannerText([cam(1), cam(2, { state: "paused", pause: pause() })], NOW))
      .toBe("Monitoring paused on Camera 2 (Cleaning) until 16:00");
    const all = pause({ camera_id: null, camera_name: "All cameras", reason_label: "Restaurant closed / off day",
      ends_at: null });
    expect(bannerText([cam(1, { state: "paused", pause: all }), cam(2, { state: "paused", pause: all })], NOW))
      .toBe("Monitoring paused on all cameras (Restaurant closed / off day) until resumed");
  });

  it("knows when the status will change next", () => {
    const status = [cam(1, { state: "planned", next_pause: pause({ starts_at: "2026-11-25T18:00:00+05:00" }) }),
      cam(2, { state: "paused", pause: pause() })];
    expect(nextChange(status, NOW)?.toISOString()).toBe(new Date("2026-11-25T16:00:00+05:00").toISOString());
  });
});
