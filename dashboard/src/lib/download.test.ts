import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_URL, ApiError, apiBlob, errorMessage, fileNameFrom, onUnauthorized, tokenStore } from "./api";

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

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => {
  vi.unstubAllGlobals();
  onUnauthorized(null);
});

describe("apiBlob() – evidence images and report downloads", () => {
  it("sends the token and returns the file with its name", async () => {
    tokenStore.set("abc");
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="RADS_report.pdf"' },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const { blob, fileName } = await apiBlob("/api/reports/4/download");
    expect(fileName).toBe("RADS_report.pdf");
    expect(blob.size).toBe(3);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}/api/reports/4/download`);
    expect(init.headers.Authorization).toBe("Bearer abc");
  });

  it("reports 410 (image removed by retention) as an ApiError with that status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Evidence was deleted by the retention policy" }), { status: 410 })));
    const err = await apiBlob("/api/evidence/9/image").then(() => null, (e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err?.status).toBe(410);
  });

  it("ends the session on 401 like every other request", async () => {
    tokenStore.set("expired");
    const handler = vi.fn();
    onUnauthorized(handler);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    await expect(apiBlob("/api/evidence/1/image")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledOnce();
    expect(tokenStore.get()).toBeNull();
  });
});

describe("helpers", () => {
  it("reads file names from Content-Disposition", () => {
    expect(fileNameFrom('attachment; filename="a b.xlsx"')).toBe("a b.xlsx");
    expect(fileNameFrom("attachment; filename=plain.pdf")).toBe("plain.pdf");
    expect(fileNameFrom(`attachment; filename*=utf-8''r%C3%A9sum%C3%A9.pdf; filename="x.pdf"`)).toBe("résumé.pdf");
    expect(fileNameFrom(null)).toBeNull();
  });

  it("drops pydantic's 'Value error, ' prefix", () => {
    expect(errorMessage({ detail: [{ msg: "Value error, period_end must not be before period_start" }] }, 422))
      .toBe("period_end must not be before period_start");
  });
});
