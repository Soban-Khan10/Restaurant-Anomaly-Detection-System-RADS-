import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, api, buildUrl, cameraStreamUrl, errorMessage, liveSocketUrl, onUnauthorized, tokenExpiry,
  tokenStore } from "./api";

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
    key: (i) => [...m.keys()][i] ?? null,
    get length() {
      return m.size;
    },
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => {
  vi.unstubAllGlobals();
  onUnauthorized(null);
});

describe("api()", () => {
  it("sends the stored token and parses JSON", async () => {
    tokenStore.set("abc");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/api/x", { query: { a: 1, empty: "", none: null, many: ["New", "Acknowledged"] } }))
      .resolves.toEqual({ ok: 1 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/api/x?a=1&many=New&many=Acknowledged`);
    expect(init.headers.Authorization).toBe("Bearer abc");
  });

  it("on 401 clears the token and calls the sign-out handler", async () => {
    tokenStore.set("expired");
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Not logged in" })));
    await expect(api("/api/incidents")).rejects.toMatchObject({ status: 401 });
    expect(tokenStore.get()).toBeNull();
    expect(handler).toHaveBeenCalledOnce();
  });

  it("does not treat a wrong password (login, auth: false) as an ended session", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Wrong username or password" })));
    const err = await api<never>("/api/auth/login", { method: "POST", body: {}, auth: false })
      .catch((e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Wrong username or password");
    expect(handler).not.toHaveBeenCalled();
  });

  it("turns server errors and network failures into readable messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(403, { detail: "Your role is not allowed to do this" })));
    await expect(api("/api/users")).rejects.toThrow("Your role is not allowed to do this");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/api/users")).rejects.toThrow("Cannot reach the RADS server");
  });

  it("returns undefined for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(204, null)));
    await expect(api("/api/users/3", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("helpers", () => {
  it("errorMessage handles FastAPI validation lists", () => {
    expect(errorMessage({ detail: [{ msg: "field required" }, { msg: "too short" }] }, 422))
      .toBe("field required; too short");
    expect(errorMessage(null, 500)).toMatch(/server had a problem/);
  });

  it("reads the expiry from a JWT", () => {
    const payload = btoa(JSON.stringify({ sub: "1", exp: 1_900_000_000 })).replace(/=+$/, "");
    expect(tokenExpiry(`h.${payload}.s`)).toBe(1_900_000_000);
    expect(tokenExpiry("not-a-jwt")).toBeNull();
  });

  it("builds live and stream URLs with the token", () => {
    expect(liveSocketUrl("a b")).toBe(`${API_URL.replace(/^http/, "ws")}/ws?token=a%20b`);
    expect(cameraStreamUrl(2, "t")).toBe(`${API_URL}/api/cameras/2/stream?token=t`);
    expect(buildUrl("/x", { page: 2 })).toBe(`${API_URL}/x?page=2`);
  });
});
