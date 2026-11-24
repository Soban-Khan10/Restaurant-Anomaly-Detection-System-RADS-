import { describe, expect, it } from "vitest";
import { navFor, rolesFor } from "../nav";
import { formatDateTime, formatDuration, formatTime, pakistanDate, pakistanDateDaysAgo, timeAgo } from "./format";

describe("Pakistan time", () => {
  it("shows server times in Asia/Karachi whatever the browser's zone", () => {
    expect(formatTime("2026-11-24T09:05:00Z")).toBe("14:05");
    expect(formatDateTime("2026-11-24T14:05:31+05:00")).toBe("24 Nov 2026, 14:05");
  });

  it("uses the Pakistan date for 'today' (19:30 UTC is already the next day there)", () => {
    expect(pakistanDate(new Date("2026-11-24T19:30:00Z"))).toBe("2026-11-25");
    expect(pakistanDateDaysAgo(6, new Date("2026-11-24T12:00:00Z"))).toBe("2026-11-18");
  });
});

describe("durations and relative times", () => {
  it("formats incident durations", () => {
    expect(formatDuration("2026-11-24T10:00:00+05:00", "2026-11-24T10:00:45+05:00")).toBe("45 s");
    expect(formatDuration("2026-11-24T10:00:00+05:00", "2026-11-24T10:03:05+05:00")).toBe("3 min 05 s");
    expect(formatDuration("2026-11-24T10:00:00+05:00", "2026-11-24T11:07:00+05:00")).toBe("1 h 07 min");
  });

  it("says how long ago", () => {
    const now = new Date("2026-11-24T10:00:00Z");
    expect(timeAgo("2026-11-24T09:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-11-24T09:48:00Z", now)).toBe("12 min ago");
    expect(timeAgo("2026-11-24T07:00:00Z", now)).toBe("3 h ago");
  });
});

describe("role-based menu (roles table)", () => {
  const labels = (role: "Admin" | "Manager" | "Technical") => navFor(role).map((n) => n.label);

  it("gives each role exactly its pages", () => {
    expect(labels("Admin")).toEqual(["Overview", "Incidents", "Cameras & areas", "Detection rules", "Reports", "Users"]);
    expect(labels("Manager")).toEqual(["Overview", "Incidents", "Reports"]);
    expect(labels("Technical")).toEqual(["Overview", "Incidents", "Cameras & areas", "Detection rules"]);
    expect(navFor(undefined)).toEqual([]);
  });

  it("guards pages by the same roles", () => {
    expect(rolesFor("/users")).toEqual(["Admin"]);
    expect(rolesFor("/reports")).toEqual(["Admin", "Manager"]);
    expect(rolesFor("/incidents/42")).toEqual(["Admin", "Manager", "Technical"]);
  });
});
