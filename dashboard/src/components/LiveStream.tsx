// Live view: the camera system's picture, relayed by the server (login required; the token goes in the URL
// because an <img> cannot send headers). When the picture cannot be shown, the reason is looked up and
// explained: 503 = camera system offline, 404 = camera turned off.
import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { cameraStreamUrl } from "../lib/api";
import { useLive } from "../live/LiveContext";
import { CameraIcon } from "./Icons";

type Problem = "offline" | "off" | "forbidden" | "error";

const PROBLEM: Record<Problem, { title: string; text: string }> = {
  offline: { title: "Camera system offline",
    text: "The live picture comes from the camera system, which is not running right now." },
  off: { title: "No live picture", text: "This camera is turned off in its settings." },
  forbidden: { title: "Live view not available", text: "Your role cannot open the live view." },
  error: { title: "The live picture could not be loaded", text: "Check the connection and try again." },
};

/** Why the stream failed: one short request, cancelled as soon as the answer's status is known. */
async function probe(url: string): Promise<Problem> {
  const ctrl = new AbortController();
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    ctrl.abort();
    if (res.status === 503) return "offline";
    if (res.status === 404) return "off";
    if (res.status === 403) return "forbidden";
    return "error";
  } catch {
    return "offline"; // the RADS server itself is not reachable
  }
}

export function LiveStream({ cameraId, name, aspect = 16 / 9 }: { cameraId: number; name: string; aspect?: number }) {
  const { token } = useAuth();
  const { cameras } = useLive();
  const [problem, setProblem] = useState<Problem | null>(null);
  const [checking, setChecking] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const heartbeat = cameras[cameraId]?.status;
  const url = token ? `${cameraStreamUrl(cameraId, token)}&_=${attempt}` : null;

  useEffect(() => setProblem(null), [cameraId]);

  async function failed() {
    if (!url) return;
    setChecking(true);
    setProblem(await probe(url));
    setChecking(false);
  }

  const shown = problem ?? (heartbeat === "offline" ? "offline" : null);
  return (
    <div className="relative w-full overflow-hidden bg-slate-900" style={{ aspectRatio: String(aspect) }}>
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-400">
        <CameraIcon className="h-8 w-8" />
        <p className="text-xs">{checking ? "Checking…" : "Connecting to the live picture…"}</p>
      </div>
      {url && !problem && !checking && (
        <img key={attempt} src={url} alt={`Live view of ${name}`} onError={failed}
          className="absolute inset-0 h-full w-full object-contain" />
      )}
      {shown && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-900 px-6
          text-center" role="status">
          <CameraIcon className="h-8 w-8 text-slate-500" />
          <p className="text-sm font-semibold text-white">{PROBLEM[shown].title}</p>
          <p className="max-w-xs text-xs text-slate-300">
            {problem ? PROBLEM[shown].text : "No signal from the camera system for 15 seconds; the picture is paused."}
          </p>
          {problem && (
            <button type="button" className="mt-1 rounded-md bg-white/10 px-3 py-1.5 text-xs font-medium text-white
              hover:bg-white/20" onClick={() => { setProblem(null); setAttempt((n) => n + 1); }}>
              Try again
            </button>
          )}
        </div>
      )}
    </div>
  );
}
