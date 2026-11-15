import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "../api";
import { la, type SimResult } from "./api";

/** Debounced POST /la/simulate that drops stale answers. */
export function useSim(key: string | null, run: () => Promise<SimResult>, delay = 200): [SimResult | null, boolean] {
  const [res, setRes] = useState<SimResult | null>(null);
  const [busy, setBusy] = useState(false);
  const n = useRef(0);
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    if (key == null) { setRes(null); return; }
    const me = ++n.current;
    setBusy(true);
    const h = window.setTimeout(() => {
      runRef.current().then((r) => { if (me === n.current) setRes(r); }).catch(() => null).finally(() => { if (me === n.current) setBusy(false); });
    }, delay);
    return () => window.clearTimeout(h);
  }, [key, delay]);
  return [res, busy];
}

const LOST_AFTER = 8; // consecutive failed polls (at 300 ms) before a run is declared lost

/** True while the backend answers. Checked every 5 s; two misses in a row mean the numbers on screen may be stale. */
export function useBackendOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    let misses = 0;
    const h = window.setInterval(() => {
      la.forecastStatus().then(() => { misses = 0; setOnline(true); }).catch(() => { if (++misses >= 2) setOnline(false); });
    }, 5000);
    return () => window.clearInterval(h);
  }, []);
  return online;
}

/** Polls one co-pilot job every 300 ms until it stops running, so the agent graph can follow it. Returns [job, start(id), clear]. */
export function usePoll(): [Job | null, (id: string) => void, () => void] {
  const [job, setJob] = useState<Job | null>(null);
  const t = useRef<number | null>(null);
  const stop = useCallback(() => { if (t.current) window.clearInterval(t.current); t.current = null; }, []);
  const start = useCallback((id: string) => {
    stop();
    setJob({ status: "running", log: [], output: null });
    let misses = 0;
    t.current = window.setInterval(async () => {
      try {
        const j = await la.job(id);
        misses = 0;
        setJob(j);
        if (j.status !== "running") stop();
      } catch {
        if (++misses < LOST_AFTER) return; // one dropped poll is not a failed run
        stop();
        setJob((j) => ({ status: "error", log: j?.log ?? [], output: null, error: "Lost contact with the backend. Check that it is running, then get plans again." }));
      }
    }, 300);
  }, [stop]);
  const clear = useCallback(() => { stop(); setJob(null); }, [stop]);
  useEffect(() => stop, [stop]);
  return [job, start, clear];
}

const KEPT_SIMS = 40; // results kept; older ones are dropped

/** Physics for several action lists at once (the options table). Each key is fetched once and kept, so selecting a
 *  row costs nothing and every row shows numbers from the same call. A key names its event, hour, conditions and actions. */
export function useSims(specs: { key: string; run: () => Promise<SimResult> }[]): Record<string, SimResult> {
  const [res, setRes] = useState<Record<string, SimResult>>({});
  const asked = useRef(new Set<string>());
  const latest = useRef(specs);
  latest.current = specs;
  const keys = specs.map((s) => s.key).join("\n");
  useEffect(() => {
    for (const sp of latest.current) {
      if (asked.current.has(sp.key)) continue;
      asked.current.add(sp.key);
      sp.run().then((r) => setRes((m) => {
        const next = { ...m, [sp.key]: r };
        const all = Object.keys(next);
        for (const old of all.slice(0, Math.max(0, all.length - KEPT_SIMS))) { delete next[old]; asked.current.delete(old); }
        return next;
      })).catch(() => { asked.current.delete(sp.key); });
    }
  }, [keys]);
  return res;
}
