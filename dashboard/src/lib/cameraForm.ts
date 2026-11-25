// Add / edit camera form: same checks as the server (app/routes/cameras.py), plus hiding the password that
// camera addresses contain.
import type { Camera } from "./types";

export interface CameraFormValues {
  name: string;
  rtsp_url: string;
  enabled: boolean;
  target_fps: string;
  frame_width: string;
  frame_height: string;
}

export type CameraFormErrors = Partial<Record<keyof CameraFormValues, string>>;

export const DEFAULT_SIZE = { width: 2688, height: 1520 };

/** rtsp://user:secret@host/... -> rtsp://user:••••••@host/... */
export function maskRtsp(url: string | undefined | null): string {
  if (!url) return "";
  return url.replace(/^([a-z][a-z0-9+.-]*:\/\/[^:/@]*):[^@/]*@/i, "$1:••••••@");
}

export function emptyCameraForm(): CameraFormValues {
  return { name: "", rtsp_url: "", enabled: true, target_fps: "5", frame_width: String(DEFAULT_SIZE.width),
    frame_height: String(DEFAULT_SIZE.height) };
}

export function formFromCamera(c: Camera): CameraFormValues {
  return { name: c.name, rtsp_url: c.rtsp_url ?? "", enabled: c.enabled, target_fps: String(c.target_fps),
    frame_width: c.frame_width == null ? "" : String(c.frame_width),
    frame_height: c.frame_height == null ? "" : String(c.frame_height) };
}

function whole(v: string): number | null {
  return /^\d+$/.test(v.trim()) ? Number(v.trim()) : null;
}

export function validateCameraForm(v: CameraFormValues): CameraFormErrors {
  const e: CameraFormErrors = {};
  const name = v.name.trim();
  if (!name) e.name = "Enter a name, for example Camera 3.";
  else if (name.length > 60) e.name = "Use at most 60 characters.";

  const url = v.rtsp_url.trim();
  let ok = false;
  try {
    const u = new URL(url);
    ok = ["rtsp:", "rtsps:", "http:", "https:"].includes(u.protocol) && !!u.hostname;
  } catch {
    ok = false;
  }
  if (!url) e.rtsp_url = "Enter the camera's address.";
  else if (!ok || url.length > 500) {
    e.rtsp_url = "Use a camera address such as rtsp://user:password@192.168.1.64:554/Streaming/Channels/101";
  }

  const fps = Number(v.target_fps);
  if (v.target_fps.trim() === "" || !Number.isFinite(fps) || fps < 1 || fps > 30) {
    e.target_fps = "Use a number from 1 to 30.";
  }

  const w = v.frame_width.trim(), h = v.frame_height.trim();
  if (w || h) {
    const wn = whole(w), hn = whole(h);
    if (wn === null || wn < 160 || wn > 7680) e.frame_width = "Width: a whole number from 160 to 7680.";
    if (hn === null || hn < 120 || hn > 4320) e.frame_height = "Height: a whole number from 120 to 4320.";
  }
  return e;
}

/** POST body (create) or PATCH body with only what changed (edit). */
export function cameraPayload(v: CameraFormValues, original?: Camera): Record<string, unknown> {
  const width = v.frame_width.trim() ? Number(v.frame_width) : null;
  const height = v.frame_height.trim() ? Number(v.frame_height) : null;
  const next = { name: v.name.trim(), rtsp_url: v.rtsp_url.trim(), enabled: v.enabled,
    target_fps: Number(v.target_fps), frame_width: width, frame_height: height };
  if (!original) return next;
  const body: Record<string, unknown> = {};
  for (const k of ["name", "rtsp_url", "enabled", "target_fps"] as const) {
    if (next[k] !== original[k]) body[k] = next[k];
  }
  if (width !== original.frame_width || height !== original.frame_height) {
    body.frame_width = width; // the server wants both sizes together
    body.frame_height = height;
  }
  return body;
}
