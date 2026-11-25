import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import type { User } from "./types";
import { emptyForm, formFromUser, serverError, userPayload, validateUserForm } from "./userForm";

const maria: User = { user_id: 5, username: "maria", role: "Manager", full_name: "Maria Khan", phone: "+923001234567",
  active: true, created_at: "2026-11-01T09:00:00+05:00", last_login: null };

describe("user form checks", () => {
  it("asks for a username and password when adding", () => {
    expect(validateUserForm(emptyForm(), "create"))
      .toEqual({ username: "Enter a username.", password: "Enter a password." });
  });

  it("explains username, phone and password rules in plain words", () => {
    const v = { ...emptyForm(), username: "maria khan", phone: "0300-12", password: "short" };
    const e = validateUserForm(v, "create");
    expect(e.username).toMatch(/no spaces/);
    expect(e.phone).toMatch(/country code/);
    expect(e.password).toBe("Use at least 8 characters.");
    expect(validateUserForm({ ...v, username: "ab" }, "create").username).toBe("Use 3 to 64 characters.");
    expect(validateUserForm({ ...v, phone: "+92 300 abc" }, "create").phone).toMatch(/digits only/);
  });

  it("accepts a valid new user, and an edit without a new password", () => {
    expect(validateUserForm({ ...emptyForm(), username: "ali.r", password: "long-enough-1", phone: "+92 300 1234567" },
      "create")).toEqual({});
    expect(validateUserForm(formFromUser(maria), "edit")).toEqual({});
  });

  it("an edit sends only what changed", () => {
    expect(userPayload(formFromUser(maria), "edit", maria)).toEqual({});
    expect(userPayload({ ...formFromUser(maria), phone: " ", role: "Admin", active: false }, "edit", maria))
      .toEqual({ phone: null, role: "Admin", active: false });
    expect(userPayload({ ...formFromUser(maria), password: "new-password-1" }, "edit", maria))
      .toEqual({ password: "new-password-1" });
  });

  it("shows a duplicate username next to the username field", () => {
    expect(serverError(new ApiError(409, "Username already taken")))
      .toEqual({ field: "username", message: "This username is already taken. Choose another one." });
    expect(serverError(new ApiError(400, "At least one active Admin must remain")))
      .toEqual({ message: "At least one active Admin must remain" });
  });
});
