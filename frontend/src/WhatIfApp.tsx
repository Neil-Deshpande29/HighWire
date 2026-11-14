import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Action, type GridView, type Job, type Terrain } from "./api";
import { HAZARD_STYLE, SCENARIO_ICON, hazardsOf } from "./hazards";
import Scene from "./components/Scene";
import SidePanel from "./components/SidePanel";

export default function WhatIfApp() {
  const [grid, setGrid] = useState<GridView | null>(null);
  const [terrain, setTerrain] = useState<Terrain | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [preview, setPreview] = useState<Action[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    Promise.all([api.state(), api.terrain()])
      .then(([g, t]) => { setGrid(g); setTerrain(t); })
      .catch(() => setError("Can't reach the backend on http://localhost:8000. Is uvicorn running?"));
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, []);

  const wrap = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }, []);

  const onToggle = useCallback((id: string) => {
    // Judge's first interaction: no busy gating, just fire and render the recomputed physics.
    api.toggle(id).then(setGrid).catch((e) => setError(String(e)));
  }, []);

  const onRun = () =>
    wrap(async () => {
      const { job_id } = await api.run();
      setJob({ status: "running", log: [], output: null });
      setPreview(null);
      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        try {
          const j = await api.job(job_id);
          setJob(j);
          if (j.status !== "running") {
            window.clearInterval(pollRef.current!);
            pollRef.current = null;
            setGrid(await api.state());
          }
        } catch (e) { setError(String(e)); }
      }, 1000);
    });

  if (!grid || !terrain) {
    return <div className="loading">{error ?? "Loading grid…"}</div>;
  }

  return (
    <div className="app">
      <div className="scene">
        <Scene grid={grid} terrain={terrain} previewActions={preview} onToggle={onToggle} overlay={overlayRef} />
        <div className="labels" ref={overlayRef} />
        <div className="legend">
          <span><i style={{ background: "#22c55e" }} />&lt;70% loading</span>
          <span><i style={{ background: "#eab308" }} />70–100%</span>
          <span><i style={{ background: "#ef4444" }} />&gt;100% / violation</span>
          <span><i style={{ background: "#6b7280" }} />open</span>
          <span><i style={{ background: "#22d3ee" }} />plan: will close</span>
          <span><i style={{ background: "#fff" }} />dashed: plan will open</span>
          <span><i style={{ background: "#f97316" }} />corroborated report</span>
          <span className="sep" />
          {(["fire", "contact", "heat", "overload", "flood", "backup", "outage"] as const).map((k) => (
            <span key={k}>{HAZARD_STYLE[k].icon} {HAZARD_STYLE[k].label}</span>
          ))}
        </div>
        <Banner grid={grid} />
      </div>
      <SidePanel
        grid={grid}
        job={job}
        busy={busy}
        error={error}
        onScenario={(n) => wrap(async () => { setJob(null); setGrid(await api.scenario(n)); })}
        onReset={() => wrap(async () => { setJob(null); setGrid(await api.reset()); })}
        onReport={(t) => wrap(async () => { setGrid((await api.report(t)).state); })}
        onRun={onRun}
        onApprove={(id) => wrap(async () => { setPreview(null); setGrid((await api.approve(id)).state); })}
        onReject={(id) => wrap(async () => { await api.reject(id); setGrid(await api.state()); })}
        onPreview={setPreview}
      />
    </div>
  );
}

function Banner({ grid }: { grid: GridView }) {
  const hz = hazardsOf(grid);
  const t = grid.sim.totals;
  return (
    <div className={`banner-map ${hz.length ? "alert" : "calm"}`}>
      <div className="bm-title">
        <span className="bm-icon">{SCENARIO_ICON[grid.scenario] ?? "⚡"}</span>
        {grid.scenario}
      </div>
      <div className="bm-desc">{grid.scenario_description}</div>
      {hz.length === 0 ? (
        <div className="bm-ok">✓ Every line and substation is within safe limits.</div>
      ) : (
        <>
          <div className="bm-head">What's happening on the grid</div>
          <ul className="bm-list">
            {hz.slice(0, 4).map((h) => (
              <li key={h.target} style={{ borderColor: HAZARD_STYLE[h.kind].color }}>
                <span>{h.icon}</span> {h.plain}
              </li>
            ))}
            {hz.length > 4 && <li className="more">+{hz.length - 4} more on the map</li>}
          </ul>
          {(t.unserved_mw > 0 || grid.sim.n1_warnings.length > 0) && (
            <div className="bm-foot">
              {t.unserved_mw > 0 && <span>⛔ {t.customers_out.toLocaleString()} customers without power</span>}
              {grid.sim.n1_warnings[0] && <span>⚠️ {grid.sim.n1_warnings[0]}</span>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
