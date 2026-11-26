import { describe, expect, it } from "vitest";
import { detectionView } from "./detectionState";

const beat = (detection?: "active" | "paused", status: "online" | "offline" | "reconnecting" = "online") =>
  ({ status, detection });

describe("detection paused: database and heartbeat", () => {
  it("both agree: no note", () => {
    expect(detectionView({ state: "paused" }, beat("paused"))).toEqual({ paused: true, applying: false });
    expect(detectionView({ state: "active" }, beat("active"))).toEqual({ paused: false, applying: false });
    expect(detectionView({ state: "planned" }, beat("active"))).toEqual({ paused: false, applying: false });
  });

  it("paused in the database, worker not reloaded yet: shown paused, applying", () => {
    expect(detectionView({ state: "paused" }, beat("active"))).toEqual({ paused: true, applying: true });
  });

  it("resumed in the database, worker still paused: shown running, applying", () => {
    expect(detectionView({ state: "active" }, beat("paused"))).toEqual({ paused: false, applying: true });
  });

  it("database status not loaded yet: the heartbeat decides", () => {
    expect(detectionView(undefined, beat("paused"))).toEqual({ paused: true, applying: false });
    expect(detectionView(undefined, beat("active"))).toEqual({ paused: false, applying: false });
    expect(detectionView(undefined, undefined)).toEqual({ paused: false, applying: false });
  });

  it("no note without a live heartbeat that reports detection", () => {
    expect(detectionView({ state: "paused" }, undefined)).toEqual({ paused: true, applying: false });
    expect(detectionView({ state: "paused" }, beat(undefined))).toEqual({ paused: true, applying: false });
    expect(detectionView({ state: "active" }, beat("paused", "offline"))).toEqual({ paused: false, applying: false });
    expect(detectionView({ state: "paused" }, beat("active", "reconnecting"))).toEqual({ paused: true, applying: true });
  });
});
