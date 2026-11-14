import { useEffect, useRef, type ReactNode } from "react";
import type { Job } from "../api";
import type { BuildingRecords } from "../la/api";
import TeamPanel, { type Ladder } from "./TeamPanel";

export type AgentChip = { id: string; name: string; summary: string };

/** Says which planner produced the plans on screen, so a rule-based run is never mistaken for the AI agent team. */
function PlannerNote({ mode, reason }: { mode: string; reason?: string | null }) {
  if (mode === "llm") return <div className="note planner">Plans by the AI agent team. Every number was re-checked by physics.</div>;
  if (reason) return <div className="note planner warn">▲ The AI team did not finish ({reason}). These plans come from the rule-based planner instead.</div>;
  return <div className="note planner">Plans by the rule-based planner (no AI key is set). Every number was checked by physics.</div>;
}

export default function PlanList({ job, chips, cards, outcome, verdict, bulletin, noSafe, records, ladder, shownPlans }: {
  job: Job | null; chips: AgentChip[]; cards: ReactNode; outcome: string | null; verdict: string | null; bulletin: string | null; noSafe: boolean;
  records: BuildingRecords | null; ladder: Ladder | null; shownPlans: string[];
}) {
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [job?.log.length]);
  const done = job?.status === "done";
  return (
    <section className="plans">
      {job?.status === "error" && <div className="error">Co-Pilot failed: {job.error}</div>}
      {outcome && <div className="card outcome"><b>Plan approved.</b> {outcome}{verdict && <div className="verdict-line">{verdict}</div>}</div>}
      {bulletin && (
        <details className="drawer card">
          <summary>Bulletin for responders · <button className="link" onClick={(e) => { e.preventDefault(); navigator.clipboard.writeText(bulletin); }}>Copy</button></summary>
          <pre className="bulletin">{bulletin}</pre>
        </details>
      )}
      {done && job.output && <PlannerNote mode={job.output.mode} reason={job.output.fallback_reason} />}
      {noSafe && <div className="note warn">▲ No safe plan was found this hour. The closest one is shown and can't be approved.</div>}
      {done && !job.output?.plans.length && <div className="note">Nothing needs switching this hour: every line is within its limits.</div>}
      {/* while the team works, watch the team; once it is done, the options come first and the team's working stays below */}
      {done && <div className="plan-cards">{cards}</div>}
      {job && <TeamPanel job={job} chips={chips} records={records} ladder={ladder} shownPlans={shownPlans} />}
      {job && (
        <details className="drawer log-drawer">
          <summary>Everything the agents logged ({job.log.length})</summary>
          <div className="log" ref={logRef}>
            {job.log.map((e, k) => <div key={k}><b>{e.agent}</b> {e.message}</div>)}
          </div>
        </details>
      )}
    </section>
  );
}
