import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

/** Charge une ressource de l'API ; `reload()` la recharge ; `intervalMs` la rafraîchit périodiquement. */
export function useApi<T>(path: string | null, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const seq = useRef(0);

  const load = useCallback(async (silent = false) => {
    if (path === null) return;
    const mine = ++seq.current;
    if (!silent) setLoading(true);
    try {
      const d = await api<T>(path);
      if (mine === seq.current) { setData(d); setError(null); }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    load();
    if (!intervalMs) return;
    const t = setInterval(() => { if (!document.hidden) load(true); }, intervalMs);
    return () => clearInterval(t);
  }, [load, intervalMs]);

  return { data, error, loading, reload: () => load(true) };
}
