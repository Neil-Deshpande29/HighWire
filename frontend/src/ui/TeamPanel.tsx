import { useEffect, useRef, useState } from "react";
import type { CopilotOutput, Job, Severity } from "../api";
import SynthesisGraph, { type NodeId } from "../desk/SynthesisGraph";
import type { BuildingRecords } from "../la/api";
import { kindPlural, plainAgentText } from "./format";

/** Inside the "Agent team" box on the left: a live graph of how the agents' findings become plans, and under it
 *  whatever the selected node read and concluded. While the team works the panel follows the agent that is busy;
 *  afterwards it opens on the coordinator's decisions.
 *
 *  Every value here was computed by the physics engine and copied by code. When the plans come from the AI model, its
 *  own free text is left out; only what it looked at is shown. */

export type AgentChip = { id: string; name: string; summary: string };
export type Ladder = { label: string; rungs: { tier: number; name: string; n: number }[] };
type Finding = NonNullable<CopilotOutput["agent_findings"]>[string][number];

const LEVEL: Record<number, { sev: Severity; mark: string; word: string }> = {
  3: { sev: "high", mark: "▲", word: "Act now" }, 2: { sev: "medium", mark: "◆", word: "Act soon" }, 1: { sev: "low", mark: "●", word: "Watch" },
};
const SHOWN = 5;

function Findings({ findings, modelText, onLine }: { findings: Finding[]; modelText: boolean; onLine: (id: string) => void }) {
  const [all, setAll] = useState(false);
  const ranked = [...findings].sort((a, b) => b.severity - a.severity);
  if (!ranked.length) return <p className="muted">Nothing to flag this hour.</p>;
  return (
    <>
      <ul className="team-findings">
        {(all ? ranked : ranked.slice(0, SHOWN)).map((f, k) => {
          const lv = LEVEL[f.severity] ?? LEVEL[1];
          return (
            <li key={`${f.target}|${f.issue}|${k}`}>
              <span className={`sev sev-${lv.sev}`} title={lv.word}>{lv.mark}</span>{" "}
              {/^L\d+$/.test(f.target) ? <button className="link" onClick={() => onLine(f.target)}>{f.target}</button> : <b>{f.target}</b>} {f.issue}
              {!modelText && f.evidence && <span className="team-params"> · {plainAgentText(f.evidence).replace(/^./, (c) => c.toLowerCase())}</span>}
            </li>
          );
        })}
      </ul>
      {ranked.length > SHOWN && <button className="link small" onClick={() => setAll((a) => !a)}>{all ? "Show fewer" : `Show all ${ranked.length}`}</button>}
    </>
  );
}

/** The Buildings agent's ladder: who is protected first, and how many of each rung an option cuts off. */
export function PriorityLadder({ records, ladder }: { records: BuildingRecords; ladder: Ladder | null }) {
  return (
    <div className="ladder">
      <div className="ladder-head"><span className="muted">Tier, most protected first</span><span className="muted">{ladder ? `cut off by ${ladder.label}` : "on record"}</span></div>
      {records.ladder.map((t) => {
        const n = ladder ? ladder.rungs.find((r) => r.tier === t.tier)?.n ?? 0 : t.n;
        return (
          <div key={t.tier} className={`rung rung-${t.tier}`} title={t.why}>
            <span className="rung-n">{t.tier}</span>
            <span className="rung-name"><b>{t.name}</b> <span className="muted">{t.kinds.map(kindPlural).join(", ")}</span></span>
            <span className={`rung-count ${ladder && n ? "hit" : ""}`}>{n}</span>
          </div>
        );
      })}
      <div className="muted small">
        {records.costar ? `CoStar: ${records.costar.toLocaleString("en-US")} properties, ${records.homes.toLocaleString("en-US")} apartment homes. ` : "CoStar kit not loaded. "}
        OpenStreetMap: {(records.n - records.costar).toLocaleString("en-US")} hospitals and water plants.
      </div>
    </div>
  );
}

/** Everything the agents wrote while working, newest at the bottom. */
export function AgentLog({ job }: { job: Job }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }); }, [job.log.length]);
  return <div className="log" ref={ref}>{job.log.map((e, k) => <div key={k}><b>{e.agent}</b> {e.message}</div>)}</div>;
}

export default function TeamPanel({ job, chips, shownPlans, selectedPlan, onPlan, onLine }: {
  job: Job; chips: AgentChip[]; shownPlans: string[]; selectedPlan: string | null; onPlan: (id: string) => void; onLine: (id: string) => void;
}) {
  const out = job.output, running = job.status === "running";
  const modelText = out?.mode === "llm";
  const [picked, setPicked] = useState<NodeId | null>(null);
  useEffect(() => { if (running) setPicked(null); }, [running]);
  // follow the work: the agent that spoke last while running, the coordinator once it is done
  const last = [...job.log].reverse().find((e) => chips.some((c) => c.id === e.agent) || e.agent === "coordinator" || e.agent === "physics");
  const focus: NodeId = picked ?? (running ? last?.agent ?? chips[0].id : "coordinator");
  const chip = chips.find((c) => c.id === focus);
  const lines = job.log.filter((e) => e.agent === focus);
  const crew = out?.crew_staging, used = out?.feedback_used ?? [];
  const planner = !out ? null : out.mode === "llm" ? "AI agent team" : out.fallback_reason ? `Rule-based planner (the AI team did not finish: ${out.fallback_reason})` : "Rule-based planner (no AI key is set)";
  return (
    <>
      <SynthesisGraph job={job} chips={chips} shownPlans={shownPlans} selectedPlan={selectedPlan} focus={focus} onFocus={setPicked} onPlan={onPlan} />
      {job.status === "error" && <div className="error">The agents stopped: {job.error}</div>}

      <div className="team-detail" aria-live="polite">
        {chip && (
          <>
            <h3>{chip.name}</h3>
            {running
              ? <p className="team-live">{lines.length ? lines[lines.length - 1].message : "Waiting for its turn."}</p>
              : <Findings findings={out?.agent_findings?.[chip.id] ?? []} modelText={modelText} onLine={onLine} />}
          </>
        )}
        {focus === "coordinator" && (
          <>
            <h3>Coordinator</h3>
            {running ? <p className="team-live">{lines.length ? lines[lines.length - 1].message : "Waiting for the agents' findings."}</p> : out && (
              <ul className="team-findings">
                {used.map((u, k) => <li key={`u${k}`}><b>Your feedback:</b> "{u.text}" <span className="team-params">→ {u.applied}</span></li>)}
                {!modelText && out.disagreements.map((d, k) => <li key={k}><b>Disagreement settled.</b> {plainAgentText(d)}</li>)}
                {crew && <li><b>Repair crews.</b> {crew.above_alarm
                  ? `Wind damage is likely: stage crews near ${crew.pre_stage_crews_near.slice(0, 4).map((l) => `${l.line} (${Math.round(l.p_damage * 100)}%)`).join(", ")}.`
                  : "No need to stage crews yet: wind damage is unlikely."}</li>}
                {!used.length && !out.disagreements.length && !crew && <li>No disagreements to settle.</li>}
              </ul>
            )}
          </>
        )}
        {focus === "physics" && (
          <>
            <h3>Physics check</h3>
            <p>{lines.length ? `${lines.map((e) => e.message.charAt(0).toUpperCase() + e.message.slice(1)).slice(-3).join(". ")}.` : "Every option the coordinator builds is run through the power-flow and fire models before it can reach you."}</p>
          </>
        )}
        {!running && <p className="muted small">Click any box in the graph to read what it found.</p>}
      </div>

      {planner && <p className={`muted small planner ${out?.fallback_reason ? "warn" : ""}`}>{planner}. Every number was checked by physics.</p>}
    </>
  );
}
