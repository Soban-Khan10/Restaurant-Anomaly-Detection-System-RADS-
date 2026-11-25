import { describe, expect, it } from "vitest";
import { cameraPayload, emptyCameraForm, formFromCamera, maskRtsp, validateCameraForm } from "./cameraForm";
import { formFromRule, rulePatch, validateRuleForm, zoneTypeFor } from "./ruleForm";
import type { Camera, Point, Rule } from "./types";
import { centre, outlineProblem, toCameraPixels } from "./zoneGeometry";

describe("zone editor: displayed size -> camera pixels", () => {
  const shown = { left: 100, top: 50, width: 896, height: 506.67 }; // a 2688 x 1520 picture shown at 1/3 size

  it("scales a click to whole camera pixels", () => {
    expect(toCameraPixels(100, 50, shown, 2688, 1520)).toEqual([0, 0]);
    expect(toCameraPixels(100 + 448, 50 + 253.335, shown, 2688, 1520)).toEqual([1344, 760]);
    expect(toCameraPixels(100 + 896, 50 + 506.67, shown, 2688, 1520)).toEqual([2688, 1520]);
  });

  it("scales with the camera's size (2688 × 1520), not the snapshot's own size (1344 × 760)", () => {
    // The clean snapshot (?raw=1) is 1344 × 760. Shown at that size, a click on its pixel (672, 380) ...
    const snapshot = { left: 0, top: 0, width: 1344, height: 760 };
    expect(toCameraPixels(672, 380, snapshot, 2688, 1520)).toEqual([1344, 760]); // ... is saved as (1344, 760)
    // Shown smaller in the editor, the same spot of the picture still gives the same camera pixel.
    const editor = { left: 250, top: 120, width: 729, height: 412.2 };
    expect(toCameraPixels(250 + 729 / 2, 120 + 412.2 / 2, editor, 2688, 1520)).toEqual([1344, 760]);
  });

  it("keeps points inside the picture when the pointer is dragged outside it", () => {
    expect(toCameraPixels(0, 0, shown, 2688, 1520)).toEqual([0, 0]);
    expect(toCameraPixels(5000, 5000, shown, 2688, 1520)).toEqual([2688, 1520]);
  });

  it("explains outlines the server would refuse", () => {
    const square: Point[] = [[100, 100], [500, 100], [500, 400], [100, 400]];
    expect(outlineProblem(square, 2688, 1520)).toBeNull();
    expect(outlineProblem(square.slice(0, 2), 2688, 1520)).toMatch(/at least 3 points/);
    expect(outlineProblem([[0, 0], [10, 10], [20, 20]], 2688, 1520)).toMatch(/no area/);
    expect(outlineProblem([[100, 100], [500, 400], [500, 100], [100, 300]], 2688, 1520)).toMatch(/crosses itself/);
    expect(outlineProblem([[100, 100], [3000, 100], [500, 400]], 2688, 1520)).toMatch(/outside/);
    expect(centre(square)).toEqual([300, 250]);
  });
});

const rule: Rule = { rule_id: 3, hazard_id: "H-01", hazard_name: "Head cover missing", camera_id: 1, enabled: true,
  conf_threshold: 0.45, window_s: 5, min_ratio: 0.7, end_gap_s: 5, cooldown_s: 60, severity: "Medium", zone_ids: [1] };

describe("detection rule form", () => {
  it("shows server values in percent and seconds, and sends back only changes", () => {
    const v = formFromRule(rule);
    expect([v.confidence, v.share, v.window]).toEqual(["45", "70", "5"]);
    expect(rulePatch(v, rule)).toEqual({});
    expect(rulePatch({ ...v, confidence: "60", window: "8", zoneIds: [2, 1] }, rule))
      .toEqual({ conf_threshold: 0.6, window_s: 8, zone_ids: [1, 2] });
  });

  it("checks the same ranges as the server, with plain messages", () => {
    const v = formFromRule(rule);
    expect(validateRuleForm(v)).toEqual({});
    const e = validateRuleForm({ ...v, confidence: "99", window: "0", share: "abc", endGap: "121", cooldown: "", zoneIds: [] });
    expect(e).toEqual({
      confidence: "Use a value from 5 to 95 %.", window: "Use a value from 1 to 30 s.", share: "Enter a number.",
      endGap: "Use a value from 1 to 120 s.", cooldown: "Enter a number.", zoneIds: "Choose at least one area.",
    });
  });

  it("knows which kind of area each hazard needs", () => {
    expect(zoneTypeFor("H-05")).toBe("waste");
    expect(["H-01", "H-04", "H-06", "H-08"].map(zoneTypeFor)).toEqual(["prep", "prep", "prep", "prep"]);
  });
});

const cam: Camera = { camera_id: 1, name: "Camera 1", enabled: true, target_fps: 5, frame_width: 2688, frame_height: 1520,
  status: "online", fps: 4.9, rtsp_url: "rtsp://rads:s3cret@192.168.1.64:554/Streaming/Channels/101" };

describe("camera form", () => {
  it("hides the camera password", () => {
    expect(maskRtsp(cam.rtsp_url)).toBe("rtsp://rads:••••••@192.168.1.64:554/Streaming/Channels/101");
    expect(maskRtsp("rtsp://192.168.1.64/stream")).toBe("rtsp://192.168.1.64/stream");
    expect(maskRtsp(undefined)).toBe("");
  });

  it("checks name, address, pictures per second and picture size", () => {
    expect(validateCameraForm({ ...emptyCameraForm(), name: "Camera 3", rtsp_url: "rtsp://u:p@10.0.0.5/1" })).toEqual({});
    const e = validateCameraForm({ name: "", rtsp_url: "ftp://x", enabled: true, target_fps: "40", frame_width: "100",
      frame_height: "" });
    expect(Object.keys(e).sort()).toEqual(["frame_height", "frame_width", "name", "rtsp_url", "target_fps"]);
  });

  it("an edit sends only what changed, and both picture sizes together", () => {
    expect(cameraPayload(formFromCamera(cam), cam)).toEqual({});
    expect(cameraPayload({ ...formFromCamera(cam), frame_width: "1920", frame_height: "1080" }, cam))
      .toEqual({ frame_width: 1920, frame_height: 1080 });
    expect(cameraPayload({ ...formFromCamera(cam), enabled: false }, cam)).toEqual({ enabled: false });
  });
});
