import { useState } from "react";
import type { CopilotOutput, Job, Severity } from "../api";
import { plainAgentText } from "./format";
import type { AgentChip } from "./PlanList";

/** The agent team at work: for each agent its status, the physical parameters it read (with their values and units)
 *  and what it concluded, then what the coordinator did with them. Shown while the run is in progress and after.
 *
 *  Every value here was computed by the physics engine and copied by code. When the plans come from the AI model, its
 *  own free text is left out of this panel; only the names of what it looked at are shown. */

const LEVEL: Record<number, { sev: Severity; mark: string; word: string }> = {
  3: { sev: "high", mark: "▲", word: "Act now" },
  2: { sev: "medium", mark: "◆", word: "Act soon" },
  1: { sev: "low", mark: "●", word: "Watch" },
};
const SHOWN = 2; // findings listed before "Show all"

type Finding = NonNullable<CopilotOutput["agent_findings"]>[string][number];

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

export default function TeamPanel({ job, chips }: { job: Job; chips: AgentChip[] }) {
  const out = job.output;
  const modelText = out?.mode === "llm";
  const screened = job.log.find((e) => e.agent === "physics" && /^screened /.test(e.message))?.message;
  const crew = out?.crew_staging;
  return (
    <div className="card team">
      <div className="eyebrow">The agent team · what each one read and found</div>
      {chips.map((c) => <Agent key={c.id} chip={c} job={job} findings={out?.agent_findings?.[c.id] ?? []} modelText={modelText} />)}
      {out && (
        <div className="team-agent done">
          <div className="team-head"><span className="team-state">✓</span><b>Coordinator</b><span className="muted">weighed the findings and tested plans</span></div>
          <ul className="team-findings">
            {screened && <li>Physics check: {screened}.</li>}
            {!modelText && out.disagreements.map((d, k) => <li key={k}><b>Disagreement.</b> {plainAgentText(d)}</li>)}
            {crew && (
              <li>
                Storm damage index {crew.storm_index.toFixed(2)} against an alarm of {crew.alarm.toFixed(2)}:{" "}
                {crew.above_alarm
                  ? `pre-stage repair crews near ${crew.pre_stage_crews_near.slice(0, 4).map((l) => `${l.line} (${Math.round(l.p_damage * 100)}%)`).join(", ")}.`
                  : "no crew pre-staging needed yet."}
              </li>
            )}
          </ul>
          {modelText && <div className="muted small">The model's own wording is left out here; values above come from the physics engine.</div>}
        </div>
      )}
    </div>
  );
}
