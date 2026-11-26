// Admin only (on the Users page): is email alerting set up, who gets it, the last result, and a test button.
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ApiError, api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { AlertStatus, TestEmailResult } from "../lib/types";
import { useApi } from "../lib/useApi";
import { isEmail } from "../lib/userForm";
import { Card, ErrorState, Spinner } from "./ui";

function Pill({ on, children }: { on: boolean; children: string }) {
  return (
    <span className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
      on ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-600 ring-slate-200"}`}>
      {children}
    </span>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function recipientsLine(managers: number, admins: number): string {
  return `${plural(managers, "Manager gets", "Managers get")} the first email; ${
    plural(admins, "Admin gets", "Admins get")} the escalation email (not reviewed for 10 minutes).`;
}

export default function EmailAlertsCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const status = useApi<AlertStatus>("/api/alerts/status");
  const [to, setTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<TestEmailResult | null>(null);

  const { reload } = status;
  useEffect(() => { if (refreshKey) reload(); }, [refreshKey, reload]);

  async function sendTest(e: FormEvent) {
    e.preventDefault();
    const address = to.trim();
    if (address && !isEmail(address)) {
      setError("Enter a valid email address, or leave it empty to use your own.");
      return;
    }
    setError(null);
    setResult(null);
    setSending(true);
    try {
      setResult(await api<TestEmailResult>("/api/alerts/test-email", { method: "POST",
        body: address ? { to: address } : {} }));
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The test email could not be sent. Please try again.");
    } finally {
      setSending(false);
    }
  }

  const email = status.data?.email;
  return (
    <Card title="Alerts" action={email && <Pill on={email.enabled}>{email.enabled ? "Email on" : "Email off"}</Pill>}>
      {status.error ? <ErrorState message={status.error} onRetry={status.reload} />
        : !email ? <Spinner />
        : (
          <div className="space-y-4 px-5 py-4 text-sm text-slate-700">
            {email.enabled ? (
              <div className="space-y-1">
                <p>
                  Sending from <span className="font-medium text-slate-900">{email.user}</span> through{" "}
                  <span className="font-medium text-slate-900">{email.host}:{email.port}</span>
                  {email.starttls ? " (secure STARTTLS)" : ""}.
                </p>
                <p>{recipientsLine(email.managers_with_email, email.admins_with_email)}</p>
                {email.managers_with_email === 0 && (
                  <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800" role="note">
                    No active Manager has an email address, so nobody gets the first email. Add one below.
                  </p>
                )}
                <p className="text-xs text-slate-500">
                  {email.dashboard_url ? `Emails link to ${email.dashboard_url}.`
                    : "Set RADS_DASHBOARD_URL in .env to add an “Open incident” button to each email."}{" "}
                  {email.attach_snapshot ? "The snapshot is attached to each email."
                    : "Snapshots are not attached (EMAIL_ATTACH_SNAPSHOT is off)."}
                </p>
              </div>
            ) : (
              <p className="rounded-lg bg-slate-50 px-3 py-2">
                Email alerts are off. Set {email.missing.join(", ")} in the .env file on the RADS computer and restart
                the app server. Alerts still appear on the dashboard.
              </p>
            )}

            <dl className="grid gap-2 sm:grid-cols-2">
              <div>
                <dt className="text-xs font-medium tracking-wide text-slate-500 uppercase">Last email alert</dt>
                <dd className="mt-0.5">
                  {email.last_alert ? (
                    <>
                      <Link className="text-brand-700 hover:underline" to={`/incidents/${email.last_alert.incident_id}`}>
                        Incident #{email.last_alert.incident_id}
                      </Link>{" "}
                      ({email.last_alert.kind === "escalation" ? "escalation" : "first alert"}) to{" "}
                      {email.last_alert.recipient ?? "nobody"} —{" "}
                      <span className={email.last_alert.status === "sent" ? "text-emerald-700" : "text-red-700"}>
                        {email.last_alert.status}
                      </span>
                      , {formatDateTime(email.last_alert.sent_at)}
                      {email.last_alert.error && <span className="block text-red-700">{email.last_alert.error}</span>}
                    </>
                  ) : "None yet"}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium tracking-wide text-slate-500 uppercase">Last test email</dt>
                <dd className="mt-0.5">
                  {email.last_test ? (
                    <>
                      To {email.last_test.to} —{" "}
                      <span className={email.last_test.status === "sent" ? "text-emerald-700" : "text-red-700"}>
                        {email.last_test.status}
                      </span>
                      , {formatDateTime(email.last_test.at)} by {email.last_test.by}
                      {email.last_test.reason && <span className="block text-red-700">{email.last_test.reason}</span>}
                    </>
                  ) : "Not since the server started"}
                </dd>
              </div>
            </dl>

            <form onSubmit={sendTest} noValidate className="flex flex-col gap-2 sm:flex-row sm:items-start">
              <div className="flex-1">
                <label htmlFor="test-email-to" className="sr-only">Send the test email to</label>
                <input id="test-email-to" type="email" className={`field ${error ? "field-invalid" : ""}`}
                  placeholder="Your own email address (or type another)" value={to} autoComplete="email"
                  onChange={(e) => { setTo(e.target.value); setError(null); }}
                  aria-invalid={!!error} aria-describedby={error ? "test-email-error" : undefined} />
                {error && <p id="test-email-error" className="field-error">{error}</p>}
              </div>
              <button type="submit" className="btn-primary whitespace-nowrap" disabled={sending || !email.enabled}
                title={email.enabled ? undefined : "Email alerts are off"}>
                {sending ? "Sending…" : "Send test email"}
              </button>
            </form>
            {result && (
              <p role="status" className={`rounded-lg px-3 py-2 ${result.status === "sent"
                ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-700"}`}>
                {result.status === "sent"
                  ? `Test email sent to ${result.to}. Check the inbox (and the spam folder).`
                  : `The test email to ${result.to} failed: ${result.reason}`}
              </p>
            )}

            <p className="text-xs text-slate-500">
              WhatsApp: {status.data!.whatsapp ? "on" : "off"}. Telegram: {status.data!.telegram ? "on" : "off"}{" "}
              (supported, but Telegram is not available in Pakistan).
            </p>
          </div>
        )}
    </Card>
  );
}
