import { describe, expect, it } from "vitest";
import { navFor, noAccessTitle, rolesFor } from "./nav";

describe("menus and page access per role", () => {
  it("Technical users never get the Reports menu or page", () => {
    expect(navFor("Technical").map((n) => n.to)).not.toContain("/reports");
    expect(rolesFor("/reports")).toEqual(["Admin", "Manager"]);
    expect(navFor("Manager").map((n) => n.to)).toContain("/reports");
    expect(navFor("Admin").map((n) => n.to)).toContain("/reports");
  });

  it("matches the roles table", () => {
    expect(navFor("Technical").map((n) => n.to)).toEqual(["/", "/incidents", "/pauses", "/cameras", "/rules"]);
    expect(navFor("Manager").map((n) => n.to)).toEqual(["/", "/incidents", "/pauses", "/rules", "/reports"]);
    expect(navFor("Admin")).toHaveLength(7);
    expect(navFor(undefined)).toEqual([]);
  });

  it("says plainly which part the role cannot open", () => {
    expect(noAccessTitle("/reports")).toBe("You don't have access to reports");
    expect(noAccessTitle("/users")).toBe("You don't have access to users");
    expect(noAccessTitle("/cameras")).toBe("You don't have access to cameras & areas");
    expect(noAccessTitle("/somewhere")).toBe("You don't have access to this page");
  });
});
