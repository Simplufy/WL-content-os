import { useCallback, useEffect, useRef, useState } from "react";

export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}K`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return String(n);
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return s < 0 ? "soon" : "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  if (s < 86400 * 365) return `${Math.floor(s / 86400 / 30)}mo ago`;
  return `${Math.floor(s / 86400 / 365)}y ago`;
}

export function mmss(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function fmtX(x: number | null | undefined): string {
  if (x === null || x === undefined) return "—";
  return x >= 10 ? `${Math.round(x)}×` : `${x.toFixed(1)}×`;
}

export function scoreTone(score: number | null | undefined): "hot" | "good" | "mid" | "low" | "none" {
  if (score === null || score === undefined) return "none";
  if (score >= 80) return "hot";
  if (score >= 65) return "good";
  if (score >= 45) return "mid";
  return "low";
}

export function outlierTone(x: number | null | undefined): "hot" | "good" | "mid" | "low" | "none" {
  if (x === null || x === undefined) return "none";
  if (x >= 3) return "hot";
  if (x >= 1.5) return "good";
  if (x >= 0.7) return "mid";
  return "low";
}

export const STATUS_LABEL: Record<string, string> = {
  listed: "Not analyzed",
  queued: "Queued",
  downloading: "Downloading",
  transcribing: "Transcribing",
  analyzing: "Analyzing",
  waiting_llm: "Waiting for Claude",
  done: "Analyzed",
  failed: "Failed",
  skipped: "Photo post",
};

export const HOOK_TYPE_LABEL: Record<string, string> = {
  question: "Question",
  bold_claim: "Bold claim",
  contrarian: "Contrarian",
  result_first: "Result first",
  story: "Story",
  mistake_warning: "Mistake / warning",
  curiosity_gap: "Curiosity gap",
  list: "List",
  demonstration: "Demonstration",
  callout: "Audience callout",
  stat: "Stat",
  other: "Other",
};

/** Fetch with loading/error state and optional polling. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    try {
      const d = await fnRef.current();
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    reload();
    if (!pollMs) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, pollMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload, setData };
}
