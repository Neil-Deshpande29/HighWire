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

const LOST_AFTER = 3; // consecutive failed polls before a run is declared lost

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

/** Polls one co-pilot job every 900 ms until it stops running. Returns [job, start(id), clear]. */
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
    }, 900);
  }, [stop]);
  const clear = useCallback(() => { stop(); setJob(null); }, [stop]);
  useEffect(() => stop, [stop]);
  return [job, start, clear];
}
