// Evidence snapshot: downloaded with the login token (an <img src> cannot send it) and shown from memory.
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, apiBlob } from "../lib/api";
import { formatDateTimeSeconds } from "../lib/format";
import type { Evidence } from "../lib/types";
import { ExpandIcon, ImageOffIcon } from "./Icons";
import { Modal } from "./Modal";

type BlobState =
  | { status: "loading" }
  | { status: "ready"; url: string }
  | { status: "removed" } // 410: deleted by the 30-day retention
  | { status: "missing" } // 404: the file is not on the server
  | { status: "error"; message: string };

/** Each successful download is one "evidence viewed" audit entry on the server, so this only runs again when
 *  the evidence itself changes (not when the rest of the incident page refreshes). */
export function useEvidenceBlob(evidence: Evidence): [BlobState, () => void] {
  const [state, setState] = useState<BlobState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const { evidence_id, deleted, image_url } = evidence;

  useEffect(() => {
    if (deleted) {
      setState({ status: "removed" });
      return;
    }
    const ctrl = new AbortController();
    let url: string | null = null;
    setState({ status: "loading" });
    apiBlob(image_url, { signal: ctrl.signal })
      .then(({ blob }) => {
        url = URL.createObjectURL(blob);
        setState({ status: "ready", url });
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        if (e instanceof ApiError && e.status === 410) setState({ status: "removed" });
        else if (e instanceof ApiError && e.status === 404) setState({ status: "missing" });
        else setState({ status: "error", message: e instanceof ApiError ? e.message : "The image could not be loaded." });
      });
    return () => {
      ctrl.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [evidence_id, deleted, image_url, attempt]);

  return [state, () => setAttempt((n) => n + 1)];
}

function Placeholder({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="flex aspect-video flex-col items-center justify-center gap-2 rounded-lg bg-slate-100 px-6 text-center">
      <ImageOffIcon className="h-9 w-9 text-slate-400" />
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {hint && <p className="max-w-sm text-xs text-slate-500">{hint}</p>}
      {children}
    </div>
  );
}

export function EvidenceView({ evidence, label }: { evidence: Evidence; label: string }) {
  const [state, retry] = useEvidenceBlob(evidence);
  const [enlarged, setEnlarged] = useState(false);
  const isClip = evidence.kind === "clip";

  let body;
  if (state.status === "loading") {
    body = (
      <div className="flex aspect-video items-center justify-center rounded-lg bg-slate-100" role="status">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
        <span className="sr-only">Loading image…</span>
      </div>
    );
  } else if (state.status === "removed") {
    body = (
      <Placeholder title="Image removed by 30-day retention"
        hint="Pictures are deleted automatically after 30 days. The incident record and its review are kept." />
    );
  } else if (state.status === "missing") {
    body = <Placeholder title="Image file not found" hint="The picture is listed but is not in the evidence folder." />;
  } else if (state.status === "error") {
    body = (
      <Placeholder title="The image could not be loaded" hint={state.message}>
        <button type="button" className="btn-secondary mt-1" onClick={retry}>Try again</button>
      </Placeholder>
    );
  } else if (isClip) {
    body = <video src={state.url} controls className="aspect-video w-full rounded-lg bg-slate-900" />;
  } else {
    body = (
      <button type="button" onClick={() => setEnlarged(true)} title="Click to enlarge"
        className="group relative block w-full overflow-hidden rounded-lg bg-slate-900">
        <img src={state.url} alt={label} className="aspect-video w-full object-contain" />
        <span className="absolute right-3 bottom-3 inline-flex items-center gap-1.5 rounded-md bg-slate-900/75 px-2.5
          py-1.5 text-xs font-medium text-white opacity-90 transition group-hover:opacity-100">
          <ExpandIcon className="h-3.5 w-3.5" /> Click to enlarge
        </span>
      </button>
    );
  }

  return (
    <figure>
      {body}
      <figcaption className="mt-2 text-xs text-slate-500">
        {isClip ? "Video clip" : "Snapshot"} taken {formatDateTimeSeconds(evidence.captured_at)}
      </figcaption>
      {enlarged && state.status === "ready" && (
        <Modal title={label} onClose={() => setEnlarged(false)} size="xl" variant="image">
          <img src={state.url} alt={label} data-autofocus tabIndex={-1}
            className="mx-auto max-h-[calc(100vh-7rem)] w-auto max-w-full rounded-lg object-contain" />
        </Modal>
      )}
    </figure>
  );
}
