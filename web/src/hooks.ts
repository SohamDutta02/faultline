import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Meta } from "./types";

export const MetaContext = createContext<Meta | null>(null);

export function useMeta(): Meta {
  const meta = useContext(MetaContext);
  if (!meta) throw new Error("meta not loaded");
  return meta;
}

/** GET a path, optionally re-polling. Keeps the previous data while refetching. */
export function useFetch<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const pathRef = useRef(path);
  pathRef.current = path;

  const load = useCallback(async () => {
    if (pathRef.current === null) return;
    const requested = pathRef.current;
    try {
      const result = await api.get<T>(requested);
      if (pathRef.current === requested) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (pathRef.current === requested) setError((e as Error).message);
    } finally {
      if (pathRef.current === requested) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(path !== null);
    load();
    if (!pollMs || path === null) return;
    const id = window.setInterval(load, pollMs);
    return () => window.clearInterval(id);
  }, [path, pollMs, load]);

  return { data, error, loading, reload: load };
}

type Theme = "system" | "light" | "dark";

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("faultline-theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("faultline-theme", theme);
    } catch {
      /* storage unavailable; the choice just won't persist */
    }
  }, [theme]);
  return [theme, setTheme];
}
