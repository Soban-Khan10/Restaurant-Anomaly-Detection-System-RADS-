import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type RequestOptions } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  /** True while a request is running (old data stays visible meanwhile). */
  loading: boolean;
  reload: () => void;
}

/** GET `path` with `query` (path null = don't fetch). Re-fetches when the path or query changes, or on
 *  reload(); a newer request aborts the older one so results never arrive out of order. */
export function useApi<T>(path: string | null, query?: RequestOptions["query"]): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [tick, setTick] = useState(0);
  const queryKey = JSON.stringify(query ?? {});

  useEffect(() => {
    if (path === null) return;
    const ctrl = new AbortController();
    setLoading(true);
    api<T>(path, { query: JSON.parse(queryKey), signal: ctrl.signal })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "Something went wrong.");
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [path, queryKey, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
