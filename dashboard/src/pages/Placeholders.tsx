// "No access" and "not found" pages.
import type { ReactNode } from "react";
import { Link } from "react-router";
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

export function NoAccess({ title = "You don't have access to this page" }: { title?: string }) {
  return (
    <Panel title={title}>
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
