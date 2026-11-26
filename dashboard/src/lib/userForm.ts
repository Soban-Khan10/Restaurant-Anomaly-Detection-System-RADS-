// The add / edit user form: checks the same rules as the server (app/routes/users.py) before sending, so the
// admin sees a plain message next to the field instead of a technical one from the server.
import { ApiError } from "./api";
import type { Role, User } from "./types";

export interface UserFormValues {
  username: string;
  full_name: string;
  role: Role;
  phone: string;
  email: string;
  password: string;
  active: boolean;
}

export type UserFormErrors = Partial<Record<keyof UserFormValues, string>>;
export type Mode = "create" | "edit";

export const PASSWORD_MIN = 8;
const PASSWORD_MAX_BYTES = 72; // bcrypt limit on the server

export function emptyForm(): UserFormValues {
  return { username: "", full_name: "", role: "Manager", phone: "", email: "", password: "", active: true };
}

export function formFromUser(u: User): UserFormValues {
  return { username: u.username, full_name: u.full_name ?? "", role: u.role, phone: u.phone ?? "", email: u.email ?? "",
    password: "",
    active: u.active };
}

/** Same rule as the server (app/email_alerts.py EMAIL_RE): one @, a dot in the domain, no spaces or quotes. */
export function isEmail(value: string): boolean {
  return value.length <= 254 && /^[^@\s<>"',;]+@[^@\s<>"',;]+\.[^@\s<>"',;]{2,}$/.test(value);
}

/** Managers get the first email, Admins the escalation; Technical users get none. */
export function getsEmailAlerts(u: Pick<User, "role" | "email" | "active">): boolean {
  return u.active && (u.role === "Manager" || u.role === "Admin") && !!u.email && isEmail(u.email.trim());
}

export function validateUserForm(v: UserFormValues, mode: Mode): UserFormErrors {
  const errors: UserFormErrors = {};
  if (mode === "create") {
    const name = v.username.trim();
    if (!name) errors.username = "Enter a username.";
    else if (name.length < 3 || name.length > 64) errors.username = "Use 3 to 64 characters.";
    else if (!/^[A-Za-z0-9_.-]+$/.test(name)) {
      errors.username = "Use only letters, numbers, dots (.), dashes (-) and underscores (_), with no spaces.";
    }
  }
  if (v.full_name.trim().length > 120) errors.full_name = "Use at most 120 characters.";

  const phone = v.phone.trim();
  if (phone) {
    const digits = phone.replace(/\D/g, "").length;
    if (phone.length > 32 || !/^\+?[\d\s()-]+$/.test(phone)) {
      errors.phone = "Use digits only, optionally starting with + (spaces and dashes are fine).";
    } else if (digits < 7 || digits > 15) {
      errors.phone = "Enter the full number with the country code, for example +92 300 1234567.";
    }
  }

  const email = v.email.trim();
  if (email && !isEmail(email)) errors.email = "Enter a valid email address, for example name@example.com.";

  if (mode === "create" && !v.password) errors.password = "Enter a password.";
  else if (v.password && v.password.length < PASSWORD_MIN) {
    errors.password = `Use at least ${PASSWORD_MIN} characters.`;
  } else if (new TextEncoder().encode(v.password).length > PASSWORD_MAX_BYTES) {
    errors.password = "This password is too long. Use at most 72 characters.";
  }
  return errors;
}

/** Body for POST /api/users (create) or PATCH /api/users/{id} with only what changed (edit). */
export function userPayload(v: UserFormValues, mode: Mode, original?: User): Record<string, unknown> {
  const fullName = v.full_name.trim() || null;
  const phone = v.phone.trim() || null;
  const email = v.email.trim() || null;
  if (mode === "create") {
    return { username: v.username.trim(), password: v.password, role: v.role, full_name: fullName, phone,
      email, active: v.active };
  }
  const body: Record<string, unknown> = {};
  if (fullName !== (original?.full_name ?? null)) body.full_name = fullName;
  if (phone !== (original?.phone ?? null)) body.phone = phone;
  if (email !== (original?.email ?? null)) body.email = email;
  if (v.role !== original?.role) body.role = v.role;
  if (v.active !== original?.active) body.active = v.active;
  if (v.password) body.password = v.password;
  return body;
}

/** Puts a server refusal next to the field it is about, when we know which one. */
export function serverError(e: unknown): { field?: keyof UserFormValues; message: string } {
  if (!(e instanceof ApiError)) return { message: "The user could not be saved. Please try again." };
  if (e.status === 409) return { field: "username", message: "This username is already taken. Choose another one." };
  return { message: e.message };
}
