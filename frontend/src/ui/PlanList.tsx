import { useEffect, useRef, type ReactNode } from "react";
import type { Job } from "../api";

export type AgentChip = { id: string; name: string; summary: string };

export default function PlanList({ job, chips, cards, outcome, bulletin, noSafe }: {
  job: Job | null; chips: AgentChip[]; cards: ReactNode; outcome: string | null; bulletin: string | null; noSafe: boolean;
}) {
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [job?.log.length]);
  const running = job?.status === "running";
  return (
    <section className="plans">
      {job && (
        <div className="chips">
          {chips.map((c) => {
            const spoke = job.log.some((e) => e.agent === c.id);
            const state = job.status === "error" ? "err" : running ? (spoke ? "busy" : "wait") : "done";
            return (
              <span key={c.id} className={`chip ${state}`}>
                {state === "done" ? "✓" : state === "err" ? "!" : <i className="spinner" />} {c.name}
                <em>{state === "done" ? c.summary : state === "busy" ? "working…" : state === "err" ? "failed" : "waiting"}</em>
              </span>
            );
          })}
        </div>
      )}
      {job?.status === "error" && <div className="error">Co-Pilot failed: {job.error}</div>}
      {outcome && <div className="card outcome"><b>Plan approved.</b> {outcome}</div>}
      {bulletin && (
        <details className="drawer card">
          <summary>Bulletin for responders · <button className="link" onClick={(e) => { e.preventDefault(); navigator.clipboard.writeText(bulletin); }}>Copy</button></summary>
          <pre className="bulletin">{bulletin}</pre>
        </details>
      )}
      {noSafe && <div className="note">No plan within 3 actions passes every physics check at this hour. The least-bad option is shown and can't be approved; a wider shutoff is the operator's call.</div>}
      {job?.status === "done" && !job.output?.plans.length && <div className="note">No switching needed at this hour: every line passes the physics checks as it is.</div>}
      <div className="plan-cards">{cards}</div>
      {job && (
        <details className="drawer log-drawer">
          <summary>Agent log ({job.log.length})</summary>
          <div className="log" ref={logRef}>
            {job.log.map((e, k) => <div key={k}><b>{e.agent}</b> {e.message}</div>)}
          </div>
        </details>
      )}
    </section>
  );
}
