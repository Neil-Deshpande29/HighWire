import { useState } from "react";
import type { CopilotOutput, Job, Severity } from "../api";
import SynthesisGraph from "../desk/SynthesisGraph";
import type { BuildingRecords } from "../la/api";
import { kindPlural, plainAgentText } from "./format";
import type { AgentChip } from "./PlanList";

/** The agent team at work: a graph of how their findings became plans, then for each agent its status, the physical
 *  values it read and what it concluded, then what the coordinator did with them (including your feedback).
 *
 *  Every value here was computed by the physics engine and copied by code. When the plans come from the AI model, its
 *  own free text is left out of this panel; only what it looked at is shown. */

const LEVEL: Record<number, { sev: Severity; mark: string; word: string }> = {
  3: { sev: "high", mark: "▲", word: "Act now" },
  2: { sev: "medium", mark: "◆", word: "Act soon" },
  1: { sev: "low", mark: "●", word: "Watch" },
};
const SHOWN = 2; // findings listed before "Show all"

type Finding = NonNullable<CopilotOutput["agent_findings"]>[string][number];
export type Ladder = { label: string; rungs: { tier: number; name: string; n: number }[] };

function Agent({ chip, job, findings, modelText }: { chip: AgentChip; job: Job; findings: Finding[]; modelText: boolean }) {
  const [all, setAll] = useState(false);
  const running = job.status === "running";
  const lines = job.log.filter((e) => e.agent === chip.id);
  const state = job.status === "error" ? "err" : running ? (lines.length ? "busy" : "wait") : "done";
  const ranked = [...findings].sort((a, b) => b.severity - a.severity);
  const shown = all ? ranked : ranked.slice(0, SHOWN);
  return (
    <div className={`team-agent ${state}`}>
      <div className="team-head">
        <span className="team-state">{state === "done" ? "✓" : state === "err" ? "!" : <i className="spinner" />}</span>
        <b>{chip.name}</b>
        <span className="muted">{state === "done" ? chip.summary : state === "busy" ? "working…" : state === "err" ? "failed" : "waiting"}</span>
      </div>
      {running && lines.length > 0 && <div className="team-live muted">{lines[lines.length - 1].message}</div>}
      {shown.length > 0 && (
        <ul className="team-findings">
          {shown.map((f, k) => {
            const lv = LEVEL[f.severity] ?? LEVEL[1];
            return (
              <li key={`${f.target}|${f.issue}|${k}`}>
                <span className={`sev sev-${lv.sev}`} title={lv.word}>{lv.mark}</span> <b>{f.target}</b> {f.issue}
                {!modelText && f.evidence && <span className="team-params"> · {plainAgentText(f.evidence).replace(/^./, (c) => c.toLowerCase())}</span>}
              </li>
            );
          })}
        </ul>
      )}
      {ranked.length > SHOWN && (
        <button className="link small" onClick={() => setAll((a) => !a)}>{all ? "Show fewer" : `Show all ${ranked.length}`}</button>
      )}
      {state === "done" && ranked.length === 0 && <div className="muted small">Nothing to flag at this hour.</div>}
    </div>
  );
}

/** The Buildings agent's ladder: who is protected first, and how many of each rung a plan puts on backup power. */
function PriorityLadder({ records, ladder }: { records: BuildingRecords; ladder: Ladder | null }) {
  return (
    <div className="ladder">
      <div className="ladder-head"><b>Who is protected first</b><span className="muted">{ladder ? `on backup in ${ladder.label}` : "buildings on record"}</span></div>
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
      <div className="muted small">{records.costar ? `${records.costar.toLocaleString("en-US")} CoStar properties and ` : "CoStar extract not loaded yet · "}{(records.n - records.costar).toLocaleString("en-US")} hospitals and water plants from OpenStreetMap</div>
    </div>
  );
}

export default function TeamPanel({ job, chips, records, ladder, shownPlans }: {
  job: Job; chips: AgentChip[]; records: BuildingRecords | null; ladder: Ladder | null; shownPlans: string[];
}) {
  const out = job.output;
  const modelText = out?.mode === "llm";
  const crew = out?.crew_staging;
  const used = out?.feedback_used ?? [];
  return (
    <div className="card team">
      <div className="eyebrow">The agent team{out ? " · how the plans were made" : " · working"}</div>
      {/* the planner can return two plans that switch the same lines; the graph shows the ones listed above */}
      {out?.synthesis && <SynthesisGraph g={{ ...out.synthesis, plans: out.synthesis.plans.filter((p) => shownPlans.includes(p.id)) }} />}
      {chips.map((c) => (
        <div key={c.id}>
          <Agent chip={c} job={job} findings={out?.agent_findings?.[c.id] ?? []} modelText={modelText} />
          {c.id === "buildings_agent" && out && records && <PriorityLadder records={records} ladder={ladder} />}
        </div>
      ))}
      {out && (
        <div className="team-agent done">
          <div className="team-head"><span className="team-state">✓</span><b>Coordinator</b><span className="muted">weighed the findings and built the plans</span></div>
          <ul className="team-findings">
            {used.map((u, k) => <li key={`u${k}`}><b>Your feedback:</b> "{u.text}" <span className="team-params">→ {u.applied}</span></li>)}
            {!modelText && out.disagreements.map((d, k) => <li key={k}><b>Disagreement.</b> {plainAgentText(d)}</li>)}
            {crew && (
              <li>
                <b>Repair crews.</b>{" "}
                {crew.above_alarm
                  ? `Wind damage is likely: stage crews near ${crew.pre_stage_crews_near.slice(0, 4).map((l) => `${l.line} (${Math.round(l.p_damage * 100)}%)`).join(", ")}.`
                  : "No need to stage crews yet: wind damage is unlikely."}
              </li>
            )}
          </ul>
          {modelText && <div className="muted small">The model's own wording is left out here; values above come from the physics engine.</div>}
        </div>
      )}
    </div>
  );
}
