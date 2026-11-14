import { useEffect, useRef, useState } from "react";
import type { Action, GridView, Job } from "../api";
import PlanCard, { TEAM_COLOR } from "./PlanCard";

export default function SidePanel(props: {
  grid: GridView; job: Job | null; busy: boolean; error: string | null;
  onScenario: (n: string) => void; onReset: () => void; onReport: (t: string) => Promise<void>;
  onRun: () => void; onApprove: (id: string) => void; onReject: (id: string) => void;
  onPreview: (a: Action[] | null) => void;
}) {
  const { grid, job } = props;
  const t = grid.sim.totals;
  const [text, setText] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const out = job?.output ?? null;
  const running = job?.status === "running";

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [job?.log.length]);

  const backupNames = t.critical_on_backup.map((id) => grid.buses.find((b) => b.id === id)?.name ?? id);
  const w = grid.weather;

  return (
    <div className="panel">
      <div className="title-row">
        <h1>⚡ Highwire <span className="muted small">what-if</span></h1>
        <span className={`mode ${grid.mode}`}>{grid.mode === "llm" ? "LLM agents" : "mock planner"}</span>
      </div>

      {/* 1. scenarios */}
      <section>
        <div className="scen">
          {grid.scenario_names.map((n) => (
            <button key={n} className={n === grid.scenario ? "active" : ""} disabled={props.busy || running} onClick={() => props.onScenario(n)}>{n}</button>
          ))}
          <button className="ghost" disabled={props.busy || running} onClick={props.onReset}>Reset</button>
        </div>
        <div className="wx">
          {(["ridge", "valley", "coast"] as const).map((z) => (
            <span key={z}><b>{z}</b> {w[z].air_temp_c}°C · gust {w[z].gust_ms} m/s · RH {w[z].humidity_pct}%{w[z].flood_level_m > 0 ? ` · flood ${w[z].flood_level_m} m` : ""}</span>
          ))}
        </div>
      </section>

      {/* 2. status strip */}
      <section className="strip">
        <div className={t.unserved_mw > 0 ? "bad" : ""}><b>{t.unserved_mw.toFixed(0)}</b><span>MW unserved</span></div>
        <div className={t.customers_out > 0 ? "bad" : ""}><b>{t.customers_out.toLocaleString()}</b><span>customers out</span></div>
        <div className={t.violation_count > 0 ? "bad" : "ok"}><b>{t.violation_count}</b><span>violations</span></div>
        <div className={backupNames.length ? "warn" : ""}><b>{backupNames.length}</b><span>critical on backup</span></div>
      </section>
      {grid.sim.violations.length > 0 && (
        <ul className="viol live">{grid.sim.violations.map((v) => <li key={v}>{v}</li>)}</ul>
      )}
      {props.error && <div className="error">{props.error}</div>}

      {/* 3. reports */}
      <section>
        <h2>Ground reports</h2>
        <form className="report-form" onSubmit={async (e) => { e.preventDefault(); if (!text.trim()) return; await props.onReport(text); setText(""); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder='e.g. "wire down on the Pine Hollow road, sparking"' />
          <button disabled={props.busy || !text.trim()}>Add report</button>
        </form>
        <div className="reports">
          {grid.reports.length === 0 && <div className="muted small">No reports yet.</div>}
          {grid.reports.map((r) => (
            <div key={r.id} className="report">
              <span className="rid">{r.id}</span>
              <span className={`tag kind`}>{r.kind.replace(/_/g, " ")}</span>
              <span className="tag">sev {r.severity}</span>
              {r.corroborated && <span className="tag corr">corroborated</span>}
              <div className="small">{r.text}</div>
              <div className="muted small">→ {r.snapped_to}</div>
            </div>
          ))}
        </div>
      </section>

      {/* 4. run + log */}
      <section>
        <button className="run" disabled={running || props.busy} onClick={props.onRun}>
          {running ? "Co-Pilot running…" : "▶ Run Co-Pilot"}
        </button>
        {job && (
          <div className="log" ref={logRef}>
            {job.log.map((e, i) => (
              <div key={i}>
                <span style={{ color: TEAM_COLOR[e.team] ?? "#9ca3af" }}>{e.agent}</span>: {e.message}
              </div>
            ))}
            {running && <div className="muted">…</div>}
          </div>
        )}
      </section>

      {/* 5. situation */}
      {out && (
        <section>
          <h2>Situation</h2>
          <p>{out.situation}</p>
          <h3>If you do nothing</h3>
          {out.do_nothing.violations.length ? (
            <ul className="viol">{out.do_nothing.violations.map((v) => <li key={v}>{v}</li>)}</ul>
          ) : <div className="muted small">No violations.</div>}
          {out.do_nothing.n1_warnings.length > 0 && <div className="muted small">N-1: {out.do_nothing.n1_warnings.join("; ")}</div>}
          {out.disagreements.length > 0 && (
            <>
              <h3>Agent disagreements</h3>
              <ul className="dis">{out.disagreements.map((d) => <li key={d}>{d}</li>)}</ul>
            </>
          )}
          {out.no_safe_option && <div className="error">No safe option found within 3 actions. The least-bad plan is shown and cannot be approved.</div>}
        </section>
      )}

      {/* 6. plans */}
      {out && out.plans.length > 0 && (
        <section>
          <h2>Plans <span className="muted small">(hover to preview on the map)</span></h2>
          {out.plans.map((p) => (
            <PlanCard key={p.id} plan={p} status={grid.plan_status[p.id]} busy={props.busy}
              onApprove={() => props.onApprove(p.id)} onReject={() => props.onReject(p.id)}
              onHover={(on) => props.onPreview(on ? p.actions : null)} />
          ))}
        </section>
      )}
      {out && out.plans.length === 0 && !out.no_safe_option && <div className="card ok">No action needed. The grid passes every physics check.</div>}

      {/* 7. bulletin */}
      {grid.bulletin && (
        <section>
          <h2>Responder bulletin</h2>
          <pre className="bulletin">{grid.bulletin.text}</pre>
          <button onClick={() => navigator.clipboard.writeText(grid.bulletin!.text)}>Copy</button>
        </section>
      )}
      <div className="muted small foot">Click any line on the map to open or close its breaker. The AI never switches anything on its own.</div>
    </div>
  );
}
