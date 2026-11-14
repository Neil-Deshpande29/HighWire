import { useRef, useState } from "react";
import type { Job, Plan } from "../api";
import type { Hour, TopLine } from "../la/api";
import { buildingsText, fmtInt, fmtPeople, mph, plainAgentText, plainViolation } from "./format";
import type { PlanNums } from "./PlanCard";
import type { AgentChip } from "./PlanList";
import type { BigNumber } from "./SituationCard";

/** One data feed the screen relies on, and what kind of thing it is. */
export type InputRow = { name: string; source: string; kind: "Live" | "Recorded" | "Modelled" | "Stand-in" | "Missing"; detail: string };

const LEVEL: Record<number, string> = { 3: "Act now", 2: "Act soon", 1: "Watch" };
const LIMITS = [
  "Lines of 66 kV and above are modelled. The smaller feeders that serve each building are not, so \"without grid power\" means the substation serving the area is cut off.",
  "Fire risk is a 0 to 1 index tuned on past outage spikes. It ranks lines; it is not the chance that a line starts a fire.",
  "Line ratings, impedances and customers per megawatt are estimates from voltage class and Census population, not utility records.",
  "Aerial photos show the day they were flown and cover each line's most fire-prone stretches, not its whole length.",
  "The co-pilot only proposes. A person approves or rejects every plan, and nothing here switches real equipment.",
];

/** A situation report for one moment: what is happening, what the team found, the options, the decision, the inputs
 *  behind it and what it cannot see. Built by code from what is on screen; no model writes any of it. */
export default function ReportPage({ title, when, basis, study, headline, numbers, hour, tops, job, chips, plans, describe, nums, statusOf, outcome, bulletin, inputs, onClose }: {
  title: string; when: string; basis: string; study: boolean; headline: string; numbers: BigNumber[]; hour: Hour; tops: TopLine[];
  job: Job | null; chips: AgentChip[]; plans: Plan[]; describe: (p: Plan) => string; nums: (p: Plan) => PlanNums;
  statusOf: (id: string) => string | undefined; outcome: string | null; bulletin: string | null; inputs: InputRow[]; onClose: () => void;
}) {
  const body = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const out = job?.status === "done" ? job.output : null;
  const made = new Date().toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  const planner = !out ? null : out.mode === "llm" ? "the AI agent team" : out.fallback_reason ? `the rule-based planner (the AI team did not finish: ${out.fallback_reason})` : "the rule-based planner (no AI key is set)";
  const screened = job?.log.find((e) => e.agent === "physics" && /^screened /.test(e.message))?.message;
  const copy = () => navigator.clipboard.writeText(body.current?.innerText ?? "").then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); });
  return (
    <div className="report">
      <div className="report-bar">
        <button className="pill" onClick={onClose}>Back to the map</button>
        <div className="spacer" />
        <button className="pill" onClick={copy}>{copied ? "Copied" : "Copy as text"}</button>
        <button className="pill" onClick={() => window.print()}>Print</button>
      </div>
      <div className="report-body" ref={body}>
        <h1>Situation report</h1>
        <p className="report-sub">{title} · {when} · {basis}{study ? " · Study mode (not the real grid)" : ""}<br />Written {made} from the data on screen. No model wrote any part of it.</p>

        <h2>1. What is happening</h2>
        <p className="report-lead">{headline}</p>
        <ul>
          {numbers.map((n) => <li key={n.label}><b>{n.value}</b> {n.label}{n.sub ? ` (${n.sub})` : ""}</li>)}
          <li>Strongest gust {mph(hour.weather.max_gust)} mph, lowest humidity {hour.weather.min_rh}%, hottest {hour.weather.max_temp} °C.</li>
          <li>{hour.redflag_active.length ? `National Weather Service Red Flag Warning in ${hour.redflag_active.length} zone${hour.redflag_active.length === 1 ? "" : "s"}.` : "No Red Flag Warning in effect."}</li>
          {hour.storm && <li>Storm damage index {hour.storm.index.toFixed(2)} against an alarm of {hour.storm.alarm.toFixed(2)}; {hour.storm.wind_hazard_pct}% of the network under damaging wind.</li>}
        </ul>

        <h2>2. Lines at risk</h2>
        {tops.length === 0 ? <p>No line is at or above the fire alarm at this hour.</p> : (
          <table>
            <thead><tr><th>Line</th><th>Voltage</th><th>Gusts</th><th>Humidity</th><th>People downwind</th><th>Vegetation near the wires</th><th>If this line is cut</th></tr></thead>
            <tbody>
              {tops.slice(0, 8).map((l) => (
                <tr key={l.id}>
                  <td><b>{l.id}</b></td><td>{Math.round(l.kv)} kV</td><td>{mph(l.gust)} mph</td><td>{Math.round(l.rh)}%</td>
                  <td>{l.downwind ? fmtPeople(l.downwind.people) : "not known"}</td>
                  <td>{l.greenery ? `${l.greenery.level} (${Math.round(l.greenery.share * 100)}% within ${l.greenery.corridor_m} m)` : "no photos yet"}</td>
                  <td>{!l.if_cut ? "not assessed" : l.if_cut.n ? `${buildingsText(l.if_cut.by_kind, ", ")} on backup; ${fmtInt(l.if_cut.customers)} customers` : "no listed building loses grid power"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2>3. What the agent team found</h2>
        {!out ? <p>The co-pilot has not been run for this hour.</p> : (
          <>
            <p>Plans came from {planner}. The physics engine re-ran every plan.</p>
            {chips.map((c) => {
              const found = [...(out.agent_findings?.[c.id] ?? [])].sort((a, b) => b.severity - a.severity);
              return (
                <div key={c.id} className="report-agent">
                  <b>{c.name}:</b> {c.summary}.
                  {out.mode !== "llm" && found.length > 0 && (
                    <ul>{found.slice(0, 6).map((f, k) => <li key={k}>{LEVEL[f.severity] ?? "Watch"}: {f.target}, {f.issue} ({plainAgentText(f.evidence).replace(/^./, (c) => c.toLowerCase())})</li>)}
                      {found.length > 6 && <li>and {found.length - 6} more</li>}</ul>
                  )}
                </div>
              );
            })}
            <div className="report-agent">
              <b>Coordinator:</b>
              <ul>
                {screened && <li>Physics check: {screened}.</li>}
                {out.mode !== "llm" && out.disagreements.map((d, k) => <li key={k}>Disagreement: {plainAgentText(d)}</li>)}
                {out.crew_staging && <li>Storm damage index {out.crew_staging.storm_index.toFixed(2)} against an alarm of {out.crew_staging.alarm.toFixed(2)}: {out.crew_staging.above_alarm ? "pre-stage repair crews" : "no crew pre-staging needed yet"}.</li>}
              </ul>
            </div>
          </>
        )}

        <h2>4. Options</h2>
        {!out ? <p>No plans yet.</p> : (
          <table>
            <thead><tr><th>Option</th><th>High-risk lines turned off</th><th>Customers losing power</th><th>Buildings without grid power</th><th>Physics check</th></tr></thead>
            <tbody>
              <tr>
                <td><b>Do nothing</b></td><td>0</td><td>{fmtInt(out.do_nothing.customers_out)}</td><td>none</td>
                <td>{out.do_nothing.violations.length ? `Fails: ${out.do_nothing.violations.length} problem${out.do_nothing.violations.length === 1 ? "" : "s"}, first ${plainViolation(out.do_nothing.violations[0])}` : "Passes"}</td>
              </tr>
              {plans.map((p) => {
                const n = nums(p), st = statusOf(p.id);
                return (
                  <tr key={p.id}>
                    <td><b>Plan {p.id.replace(/^P/, "")}</b>{st && st !== "pending" ? ` (${st})` : ""}: {describe(p)}</td>
                    <td>{n.highRiskOff}</td><td>{fmtInt(n.customers)}</td>
                    <td>{p.result.buildings_out?.n ? buildingsText(p.result.buildings_out.by_kind, ", ") : "none listed"}</td>
                    <td>{n.ok ? "Passes" : `Fails: ${n.reason}`}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <h2>5. Decision</h2>
        {outcome ? <p><b>Plan approved.</b> {outcome}</p> : <p>No plan has been approved for this hour.</p>}
        {bulletin && <pre className="bulletin">{bulletin}</pre>}

        <h2>6. Inputs and how fresh they are</h2>
        <table>
          <thead><tr><th>Input</th><th>Source</th><th>Kind</th><th>Note</th></tr></thead>
          <tbody>{inputs.map((r) => <tr key={r.name}><td><b>{r.name}</b></td><td>{r.source}</td><td><span className={`kind kind-${r.kind.toLowerCase()}`}>{r.kind}</span></td><td>{r.detail}</td></tr>)}</tbody>
        </table>

        <h2>7. What this report cannot see</h2>
        <ul>{LIMITS.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
    </div>
  );
}
