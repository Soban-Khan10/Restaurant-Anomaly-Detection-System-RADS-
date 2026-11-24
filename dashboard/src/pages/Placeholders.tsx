// Screens that come in the next dashboard steps (6b onwards), plus "no access" and "not found".
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { Card } from "../components/ui";

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <div className="px-6 py-12 text-center">
        <p className="text-base font-semibold text-slate-900">{title}</p>
        <div className="mx-auto mt-2 max-w-md text-sm text-slate-500">{children}</div>
      </div>
    </Card>
  );
}

export function ComingSoon({ title }: { title: string }) {
  return (
    <Panel title={title}>
      This screen will be added in the next step of the dashboard.
    </Panel>
  );
}

export function IncidentDetailPlaceholder() {
  const { id } = useParams();
  return (
    <Panel title={`Incident #${id}`}>
      <p>The full incident page, with the snapshot, rule values and review buttons, will be added in the next step.</p>
      <Link to="/incidents" className="mt-4 inline-block font-medium text-brand-700 hover:underline">
        ← Back to incidents
      </Link>
    </Panel>
  );
}

export function NoAccess() {
  return (
    <Panel title="You don't have access to this page">
      Your role does not include this part of RADS. Ask an administrator if you need it.
      <div><Link to="/" className="mt-4 inline-block font-medium text-brand-700 hover:underline">Go to overview</Link></div>
    </Panel>
  );
}

export function NotFound() {
  return (
    <Panel title="Page not found">
      <Link to="/" className="font-medium text-brand-700 hover:underline">Go to overview</Link>
    </Panel>
  );
}
