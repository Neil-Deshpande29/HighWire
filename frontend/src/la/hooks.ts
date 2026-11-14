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

/** Polls one co-pilot job every 900 ms until it stops running. Returns [job, start(id), clear]. */
export function usePoll(): [Job | null, (id: string) => void, () => void] {
  const [job, setJob] = useState<Job | null>(null);
  const t = useRef<number | null>(null);
  const stop = useCallback(() => { if (t.current) window.clearInterval(t.current); t.current = null; }, []);
  const start = useCallback((id: string) => {
    stop();
    setJob({ status: "running", log: [], output: null });
    t.current = window.setInterval(async () => {
      try { const j = await la.job(id); setJob(j); if (j.status !== "running") stop(); } catch { stop(); }
    }, 900);
  }, [stop]);
  const clear = useCallback(() => { stop(); setJob(null); }, [stop]);
  useEffect(() => stop, [stop]);
  return [job, start, clear];
}
