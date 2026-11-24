// API client: keeps the login token, adds it to every request, and sends the user back to the
// login page when the server answers 401 (token missing, expired, or the account was deactivated).

export const API_URL: string = (import.meta.env?.VITE_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const TOKEN_KEY = "rads.token";

/** The token lives in localStorage so a page reload keeps the user signed in (the token itself expires
 *  after 8 hours). Every access is guarded: storage can be blocked in private windows. */
export const tokenStore = {
  get(): string | null {
    try {
      return globalThis.localStorage?.getItem(TOKEN_KEY) ?? null;
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      globalThis.localStorage?.setItem(TOKEN_KEY, token);
    } catch {
      /* the session then lasts until the tab is closed */
    }
  },
  clear(): void {
    try {
      globalThis.localStorage?.removeItem(TOKEN_KEY);
    } catch {
      /* nothing to clear */
    }
  },
};

/** Seconds since 1970 at which the JWT expires, or null if it can't be read. */
export function tokenExpiry(token: string): number | null {
  try {
    const part = token.split(".")[1];
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "="));
    const exp = JSON.parse(json).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Called once by the auth provider: what to do when any request comes back 401. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** FastAPI errors are {"detail": "text"} or {"detail": [{"msg": ...}, ...]} for validation problems. */
export function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d as { msg?: string }).msg).filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  if (status >= 500) return "The RADS server had a problem. Please try again.";
  return `Request failed (${status}).`;
}

type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function buildUrl(path: string, query?: Query): string {
  const url = new URL(API_URL + path);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    for (const v of Array.isArray(value) ? value : [value]) url.searchParams.append(key, String(v));
  }
  return url.toString();
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  signal?: AbortSignal;
  /** false for the login call itself: a 401 there means "wrong password", not "session ended". */
  auth?: boolean;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal, auth = true } = opts;
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenStore.get();
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the RADS server. Check that it is running.");
  }

  if (res.status === 401 && auth) {
    tokenStore.clear();
    unauthorizedHandler?.();
    throw new ApiError(401, "Your session has ended. Please sign in again.");
  }
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, res.status));
  return data as T;
}

/** WebSocket address for live updates (message 3). */
export function liveSocketUrl(token: string): string {
  return API_URL.replace(/^http/, "ws") + "/ws?token=" + encodeURIComponent(token);
}

/** MJPEG live view; the token goes in the URL because an <img> tag cannot send headers. */
export function cameraStreamUrl(cameraId: number, token: string): string {
  return buildUrl(`/api/cameras/${cameraId}/stream`, { token });
}
