import { useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "../auth/AuthContext";
import EmailAlertsCard from "../components/EmailAlertsCard";
import { CheckIcon, PlusIcon } from "../components/Icons";
import { Modal } from "../components/Modal";
import { Card, EmptyState, ErrorState, Spinner } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { ROLE_LABEL, formatDateTime } from "../lib/format";
import type { Role, User } from "../lib/types";
import { useApi } from "../lib/useApi";
import {
  PASSWORD_MIN, emptyForm, formFromUser, getsEmailAlerts, serverError, userPayload, validateUserForm, type Mode, type UserFormErrors,
  type UserFormValues,
} from "../lib/userForm";

const ROLES: Role[] = ["Admin", "Manager", "Technical"];
const ROLE_CAN: Record<Role, string> = {
  Admin: "Can do everything, including managing users, cameras and rules. Gets the escalation email.",
  Manager: "Reviews incidents, gets the first alert email and creates reports.",
  Technical: "Sets up cameras, areas and detection rules. Cannot review incidents.",
};

const displayName = (u: User) => u.full_name || u.username;

// ---------------------------------------------------------------- add / edit form

function FormField({ id, label, hint, error, children }:
  { id: string; label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="field-label">{label}</label>
      {children}
      {error ? <p id={`${id}-error`} className="field-error">{error}</p>
        : hint ? <p id={`${id}-hint`} className="field-hint">{hint}</p> : null}
    </div>
  );
}

function UserForm({ mode, user, isSelf, onClose, onSaved }: {
  mode: Mode; user?: User; isSelf: boolean; onClose: () => void; onSaved: (u: User, message: string) => void;
}) {
  const [values, setValues] = useState<UserFormValues>(() => (user ? formFromUser(user) : emptyForm()));
  const [errors, setErrors] = useState<UserFormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const formId = "user-form";

  const set = <K extends keyof UserFormValues>(key: K, value: UserFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  };
  const invalid = (k: keyof UserFormValues) => (errors[k] ? "field-invalid" : "");
  const describedBy = (id: string, k: keyof UserFormValues) => (errors[k] ? `${id}-error` : `${id}-hint`);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const found = validateUserForm(values, mode);
    setErrors(found);
    if (Object.keys(found).length) return;

    const body = userPayload(values, mode, user);
    if (mode === "edit" && Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    try {
      const saved = mode === "create"
        ? await api<User>("/api/users", { method: "POST", body })
        : await api<User>(`/api/users/${user!.user_id}`, { method: "PATCH", body });
      onSaved(saved, mode === "create" ? `${displayName(saved)} can now sign in as "${saved.username}".`
        : `Changes to ${displayName(saved)} were saved.`);
    } catch (err) {
      const { field, message } = serverError(err);
      if (field) setErrors((x) => ({ ...x, [field]: message }));
      else setFormError(message);
      setSaving(false);
    }
  }

  return (
    <Modal title={mode === "create" ? "Add a user" : `Edit ${user ? displayName(user) : "user"}`} onClose={onClose}
      busy={saving} footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
          <button type="submit" form={formId} className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : mode === "create" ? "Add user" : "Save changes"}
          </button>
        </>
      }>
      <form id={formId} onSubmit={submit} noValidate className="space-y-4">
        {formError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{formError}</p>}

        {mode === "create" ? (
          <FormField id="u-username" label="Username" error={errors.username}
            hint="Used to sign in. Letters, numbers, dots, dashes or underscores. It cannot be changed later.">
            <input id="u-username" className={`field ${invalid("username")}`} autoComplete="off" autoFocus
              value={values.username} onChange={(e) => set("username", e.target.value)}
              aria-invalid={!!errors.username} aria-describedby={describedBy("u-username", "username")} />
          </FormField>
        ) : (
          <div>
            <p className="field-label">Username</p>
            <p className="text-sm font-medium text-slate-900">{user?.username}</p>
          </div>
        )}

        <FormField id="u-name" label="Full name" error={errors.full_name}>
          <input id="u-name" className={`field ${invalid("full_name")}`} autoComplete="off" value={values.full_name}
            onChange={(e) => set("full_name", e.target.value)} aria-invalid={!!errors.full_name}
            autoFocus={mode === "edit"} />
        </FormField>

        <FormField id="u-role" label="Role" error={errors.role}
          hint={isSelf ? "You cannot change your own role." : ROLE_CAN[values.role]}>
          <select id="u-role" className="field" value={values.role} disabled={isSelf}
            onChange={(e) => set("role", e.target.value as Role)} aria-describedby="u-role-hint">
            {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </select>
        </FormField>

        <FormField id="u-phone" label="Phone (optional)" error={errors.phone}
          hint="For WhatsApp alerts. Include the country code, for example +92 300 1234567.">
          <input id="u-phone" type="tel" className={`field ${invalid("phone")}`} autoComplete="off"
            value={values.phone} onChange={(e) => set("phone", e.target.value)}
            aria-invalid={!!errors.phone} aria-describedby={describedBy("u-phone", "phone")} />
        </FormField>

        <FormField id="u-email" label="Email (optional)" error={errors.email}
          hint={values.role === "Technical" ? "Technical users do not get alert emails."
            : values.role === "Admin" ? "Gets an email when an incident is not reviewed for 10 minutes."
            : "Gets an email for every new incident."}>
          <input id="u-email" type="email" className={`field ${invalid("email")}`} autoComplete="off"
            value={values.email} onChange={(e) => set("email", e.target.value)}
            aria-invalid={!!errors.email} aria-describedby={describedBy("u-email", "email")} />
        </FormField>

        <FormField id="u-password" label={mode === "create" ? "Password" : "New password (optional)"}
          error={errors.password}
          hint={mode === "create" ? `At least ${PASSWORD_MIN} characters. Give it to the person privately.`
            : "Leave empty to keep the current password."}>
          <input id="u-password" type="password" className={`field ${invalid("password")}`} autoComplete="new-password"
            value={values.password} onChange={(e) => set("password", e.target.value)}
            aria-invalid={!!errors.password} aria-describedby={describedBy("u-password", "password")} />
        </FormField>

        <div>
          <label className="flex items-start gap-3">
            <input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-brand-600"
              checked={values.active} disabled={isSelf} onChange={(e) => set("active", e.target.checked)} />
            <span>
              <span className="block text-sm font-medium text-slate-800">Account active</span>
              <span className="block text-xs text-slate-500">
                {isSelf ? "You cannot deactivate your own account." : "Inactive users cannot sign in or get alerts."}
              </span>
            </span>
          </label>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- deactivate

function ConfirmDeactivate({ user, onClose, onDone }:
  { user: User; onClose: () => void; onDone: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = displayName(user);

  async function deactivate() {
    setBusy(true);
    setError(null);
    try {
      await api<void>(`/api/users/${user.user_id}`, { method: "DELETE" });
      onDone(`${name} was deactivated and can no longer sign in.`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The user could not be deactivated.");
      setBusy(false);
    }
  }

  return (
    <Modal title={`Deactivate ${name}?`} size="sm" onClose={onClose} busy={busy} footer={
      <>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy} data-autofocus>Cancel</button>
        <button type="button" className="btn-danger" onClick={deactivate} disabled={busy}>
          {busy ? "Deactivating…" : "Deactivate"}
        </button>
      </>
    }>
      <div className="space-y-3 text-sm text-slate-600">
        <p>
          {name} will no longer be able to sign in and will stop getting alerts. Their past reviews and reports are
          kept.
        </p>
        <p>You can turn the account back on later.</p>
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">{error}</p>}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- page

export default function UsersPage() {
  const { user: me } = useAuth();
  const users = useApi<User[]>("/api/users");
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [deactivating, setDeactivating] = useState<User | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [changes, setChanges] = useState(0); // the Alerts card recounts who gets email after a change

  /** Show the change at once (with the success message), then reload the list from the server. */
  const done = (message: string, changed: User) => {
    const current = users.data ?? [];
    users.mutate(current.some((x) => x.user_id === changed.user_id)
      ? current.map((x) => (x.user_id === changed.user_id ? changed : x)) : [...current, changed]);
    setEditing(null);
    setDeactivating(null);
    setFlash(message);
    setRowError(null);
    users.reload();
    setChanges((n) => n + 1);
  };

  async function reactivate(u: User) {
    setBusyId(u.user_id);
    setRowError(null);
    try {
      const saved = await api<User>(`/api/users/${u.user_id}`, { method: "PATCH", body: { active: true } });
      done(`${displayName(u)} can sign in again.`, saved);
    } catch (e) {
      setRowError(e instanceof ApiError ? e.message : "The user could not be reactivated.");
    } finally {
      setBusyId(null);
    }
  }

  // Active users first, then by name.
  const list = [...(users.data ?? [])].sort((a, b) =>
    Number(b.active) - Number(a.active) || displayName(a).localeCompare(displayName(b)));

  return (
    <div className="space-y-4">
      {flash && (
        <div className="flex items-start justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4
          py-3 text-sm text-emerald-800" role="status">
          <span className="flex items-center gap-2"><CheckIcon className="h-4 w-4 shrink-0" /> {flash}</span>
          <button type="button" className="text-emerald-700 hover:underline" onClick={() => setFlash(null)}>
            Dismiss
          </button>
        </div>
      )}
      {rowError && <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">{rowError}</p>}

      <EmailAlertsCard refreshKey={changes} />

      <Card title="People who can sign in to RADS" action={
        <button type="button" className="btn-primary shrink-0 whitespace-nowrap !py-1.5"
          onClick={() => { setFlash(null); setEditing("new"); }}>
          <PlusIcon className="h-4 w-4" /> Add user
        </button>
      }>
        {users.error ? <ErrorState message={users.error} onRetry={users.reload} />
          : !users.data ? <Spinner />
          : list.length === 0 ? <EmptyState title="No users yet" />
          : (
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[56rem] text-left text-sm">
                <thead className="border-b border-slate-200 bg-slate-50 text-xs font-medium tracking-wide text-slate-500
                  uppercase">
                  <tr>
                    <th scope="col" className="px-5 py-3">Name</th>
                    <th scope="col" className="px-4 py-3">Role</th>
                    <th scope="col" className="px-4 py-3">Email</th>
                    <th scope="col" className="px-4 py-3">Phone</th>
                    <th scope="col" className="px-4 py-3">Status</th>
                    <th scope="col" className="px-4 py-3">Last signed in</th>
                    <th scope="col" className="px-5 py-3 text-right"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {list.map((u) => {
                    const self = u.user_id === me?.user_id;
                    return (
                      <tr key={u.user_id} className={u.active ? "" : "bg-slate-50/60"}>
                        <td className="px-5 py-3">
                          <p className={`font-medium ${u.active ? "text-slate-900" : "text-slate-500"}`}>
                            {displayName(u)}
                            {self && <span className="ml-2 text-xs font-normal text-slate-500">(you)</span>}
                          </p>
                          <p className="text-xs text-slate-500">{u.username}</p>
                        </td>
                        <td className="px-4 py-3 text-slate-700">{ROLE_LABEL[u.role]}</td>
                        <td className="px-4 py-3 text-slate-700">
                          <p className="break-all">{u.email || "—"}</p>
                          {getsEmailAlerts(u) && (
                            <p className="mt-0.5 inline-flex rounded-full bg-brand-50 px-2 py-0.5 text-xs font-medium
                              text-brand-800 ring-1 ring-brand-200 ring-inset">gets email alerts</p>
                          )}
                        </td>
                        <td className="px-4 py-3 whitespace-nowrap text-slate-700">{u.phone || "—"}</td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
                            u.active ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                              : "bg-slate-100 text-slate-600 ring-slate-200"}`}>
                            {u.active ? "Active" : "Inactive"}
                          </span>
                        </td>
                        <td className="px-4 py-3 whitespace-nowrap text-slate-600">
                          {u.last_login ? formatDateTime(u.last_login) : "Never"}
                        </td>
                        <td className="px-5 py-3">
                          <div className="flex justify-end gap-2">
                            <button type="button" className="btn-secondary !py-1 text-xs"
                              onClick={() => { setFlash(null); setEditing(u); }}
                              aria-label={`Edit ${displayName(u)}`}>
                              Edit
                            </button>
                            {u.active ? (
                              !self && (
                                <button type="button" className="btn-secondary !py-1 text-xs text-red-700
                                  hover:!bg-red-50" onClick={() => { setFlash(null); setDeactivating(u); }}
                                  aria-label={`Deactivate ${displayName(u)}`}>
                                  Deactivate
                                </button>
                              )
                            ) : (
                              <button type="button" className="btn-secondary !py-1 text-xs"
                                disabled={busyId === u.user_id} onClick={() => reactivate(u)}
                                aria-label={`Reactivate ${displayName(u)}`}>
                                {busyId === u.user_id ? "Reactivating…" : "Reactivate"}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      {editing && (
        <UserForm mode={editing === "new" ? "create" : "edit"} user={editing === "new" ? undefined : editing}
          isSelf={editing !== "new" && editing.user_id === me?.user_id}
          onClose={() => setEditing(null)} onSaved={(saved, message) => done(message, saved)} />
      )}
      {deactivating && (
        <ConfirmDeactivate user={deactivating} onClose={() => setDeactivating(null)}
          onDone={(message) => done(message, { ...deactivating, active: false })} />
      )}
    </div>
  );
}
