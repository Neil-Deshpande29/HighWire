import { useRef, useState } from "react";
import type { Job } from "../api";
import { CONE_NOTE, FEEDER_NOTE, INDEX_NOTE, SCORES_NOTE } from "../desk/marks";
import type { Option } from "../desk/OptionsTable";
import { sensorsSentence, type InputRow } from "../desk/sheets";
import type { Hour, RecordEntry, RiskRegister, Sensors, TopLine } from "../la/api";
import { buildingsText, fmtInt, fmtPeople, mph, plainAgentText, plainViolation, timeLabel } from "./format";
import type { AgentChip } from "./TeamPanel";
import type { BigNumber } from "../la/derive";

const LEVEL: Record<number, string> = { 3: "Act now", 2: "Act soon", 1: "Watch" };
const AUTH: Record<string, string> = { operator: "Operator", supervisor: "Supervisor sign-off", blocked: "Nobody: fails physics" };
const LIMITS = [
  FEEDER_NOTE,
  INDEX_NOTE,
  "Line ratings, impedances and customers per megawatt are estimates from voltage class and Census population, not utility records.",
  "Aerial photos show the day they were flown and cover each line's most fire-prone stretches, not its whole length.",
  "Weather stations sit at airports in the basin and valleys. None is on a ridge, where gusts are strongest, so agreement at the stations does not prove the model is right in the mountains.",
  "Approval limits are this project's demo protocol, not a utility's policy. Dollar figures are left out: the outage-cost rates behind them have not been checked against their sources.",
  "The co-pilot only proposes. A person approves or rejects every plan, and nothing here switches real equipment.",
];

/** A situation report for one moment: what is happening, the risk analysis, what the sensors say, what the team found,
 *  the options with what each buys and costs, the decision, the inputs behind it and what it cannot see.
 *  Built by code from what is on screen; no model writes any of it. */
export default function ReportPage({ title, when, basis, study, headline, numbers, hour, tops, job, chips, options, describe, outcome, bulletin, inputs, sensors, register, record, onClose }: {
  title: string; when: string; basis: string; study: boolean; headline: string; numbers: BigNumber[]; hour: Hour; tops: TopLine[];
  job: Job | null; chips: AgentChip[]; options: Option[]; describe: (o: Option) => string; outcome: string | null; bulletin: string | null;
  inputs: InputRow[]; sensors: Sensors | null; register: RiskRegister | null; record: RecordEntry[]; onClose: () => void;
}) {
  const body = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  const out = job?.status === "done" ? job.output : null;
  const made = new Date().toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  const planner = !out ? null : out.mode === "llm" ? "the AI agent team" : out.fallback_reason ? `the rule-based planner (the AI team did not finish: ${out.fallback_reason})` : "the rule-based planner (no AI key is set)";
  const screened = job?.log.find((e) => e.agent === "physics" && /^screened /.test(e.message))?.message;
  const copy = () => navigator.clipboard.writeText(body.current?.innerText ?? "").then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); });
  const nothing = options.find((o) => o.kind === "nothing")?.sim ?? null;
  const pick = options.find((o) => o.kind === "plan" && o.sim?.is_safe) ?? null;
  const forHour = record.filter((e) => e.hour_utc && Math.abs(new Date(e.hour_utc).getTime() - new Date(hour.t_utc).getTime()) < 1800e3);
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
          {numbers.map((n) => <li key={n.label}>{n.value === "?" ? <>Customers out now: not shown, because what happened stays hidden until someone chooses to see it.</> : <><b>{n.value}</b> {n.label}{n.sub ? ` (${n.sub})` : ""}</>}</li>)}
          <li>Strongest gust {mph(hour.weather.max_gust)} mph, lowest humidity {hour.weather.min_rh}%, hottest {hour.weather.max_temp} °C.</li>
          <li>{hour.redflag_active.length ? `National Weather Service Red Flag Warning in ${hour.redflag_active.length} zone${hour.redflag_active.length === 1 ? "" : "s"}.` : "No Red Flag Warning in effect."}</li>
          {hour.storm && <li>Storm damage index {hour.storm.index.toFixed(2)} against an alarm of {hour.storm.alarm.toFixed(2)}; {hour.storm.wind_hazard_pct}% of the network under damaging wind.</li>}
        </ul>

        <h2>2. Risk analysis</h2>
        {register ? (
          <>
            <p>{register.n_high} line{register.n_high === 1 ? " is" : "s are"} at or above the fire alarm (index {register.alarm.toFixed(2)}); {register.n_elevated} more {register.n_elevated === 1 ? "is" : "are"} elevated.
              Of the lines at high risk, {register.matrix.high.high.length} have a hospital or {fmtPeople(register.bands.people_high)} or more people downwind,
              {" "}{register.matrix.high.medium.length} have a town or a water plant downwind, and {register.matrix.high.low.length} have few people downwind.</p>
            <table>
              <thead><tr><th>Line</th><th>Fire index</th><th>Gusts</th><th>Humidity</th><th>Trees near the wires</th><th>If left on: downwind</th><th>If turned off</th></tr></thead>
              <tbody>
                {register.rows.slice(0, 12).map((r) => (
                  <tr key={r.id}>
                    <td><b>{r.id}</b> {Math.round(r.kv)} kV</td><td>{r.index.toFixed(2)} ({r.ratio.toFixed(2)}× alarm)</td><td>{mph(r.gust_ms)} mph</td><td>{Math.round(r.rh_pct)}%</td>
                    <td>{r.trees ? `${r.trees.trees} trees (Vexcel)` : r.greenery ? `${r.greenery.level} (${Math.round(r.greenery.share * 100)}% green)` : "not measured"}</td>
                    <td>{fmtPeople(r.downwind.people)} people{r.downwind.hospitals ? `, ${r.downwind.hospitals} hospital${r.downwind.hospitals === 1 ? "" : "s"}` : ""}{r.downwind.water_plants ? `, ${r.downwind.water_plants} water plant${r.downwind.water_plants === 1 ? "" : "s"}` : ""}</td>
                    <td>{r.class === "bulk" ? "import line: kept on, patrolled" : !r.if_cut || (!r.if_cut.n && !r.if_cut.customers) ? "nothing loses grid power"
                      : `${r.if_cut.n ? `${buildingsText(r.if_cut.by_kind, ", ")} on backup; ` : ""}${fmtInt(r.if_cut.customers)} customers`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {register.rows.length > 12 && <p className="report-note">The 12 riskiest of {register.rows.length} lines are listed.</p>}
            <p className="report-note">{SCORES_NOTE} {CONE_NOTE}</p>
          </>
        ) : tops.length === 0 ? <p>No line is at or above the fire alarm at this hour.</p> : (
          <ul>{tops.slice(0, 8).map((l) => <li key={l.id}><b>{l.id}</b> ({Math.round(l.kv)} kV): gusts {mph(l.gust)} mph, humidity {Math.round(l.rh)}%{l.downwind ? `, ${fmtPeople(l.downwind.people)} people downwind` : ""}.</li>)}</ul>
        )}

        <h2>3. What the weather stations measured</h2>
        {!sensors ? <p>The station check was not available when this report was written.</p> : (
          <>
            <p>{sensorsSentence(sensors)}</p>
            <table>
              <thead><tr><th>Station</th><th>Gust measured</th><th>Gust in the model</th><th>Humidity measured</th><th>Humidity in the model</th><th>Result</th></tr></thead>
              <tbody>
                {sensors.stations.map((st) => (
                  <tr key={st.id}>
                    <td><b>{st.name}</b></td>
                    <td>{st.measured?.gust_ms != null ? `${mph(st.measured.gust_ms)} mph` : "no report"}</td><td>{st.model.gust_ms != null ? `${mph(st.model.gust_ms)} mph` : "n/a"}</td>
                    <td>{st.measured?.rh_pct != null ? `${Math.round(st.measured.rh_pct)}%` : "no report"}</td><td>{st.model.rh_pct != null ? `${Math.round(st.model.rh_pct)}%` : "n/a"}</td>
                    <td>{st.flag === "model_low" ? "Measured stronger than the model" : st.flag === "model_high" ? "Model stronger (cautious side)" : st.flag === "agrees" ? "Agrees" : "No report"}{st.drier ? "; drier than the model" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="report-note">{sensors.source}; {sensors.mode === "live" ? "latest report from each station" : `reports from the hour ending ${timeLabel(sensors.t_utc, { weekday: "short", hour: "numeric", minute: "2-digit" })}`}.</p>
          </>
        )}

        <h2>4. What the agent team found</h2>
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

        <h2>5. Options, and what each buys and costs</h2>
        {!options.length ? <p>No plans yet.</p> : (
          <>
            <table>
              <thead><tr><th>Option</th><th>High-risk lines still on</th><th>People downwind of them</th><th>Customers without power</th><th>Critical sites on backup</th><th>Physics check</th><th>Who may approve</th></tr></thead>
              <tbody>
                {options.map((o) => {
                  const r = o.sim;
                  const auth = o.plan?.authority ?? r?.authority;
                  return (
                    <tr key={o.key}>
                      <td><b>{o.label}</b>{o.status && o.status !== "pending" ? ` (${o.status})` : ""}: {describe(o)}</td>
                      {!r ? <td colSpan={6}>not computed</td> : (
                        <>
                          <td>{r.situation.n_high_risk}</td><td>{fmtPeople(r.situation.people_downwind)}</td><td>{fmtInt(r.customers_out)}</td>
                          <td>{r.buildings_out?.n ? buildingsText(r.buildings_out.by_kind, ", ") : "none"}</td>
                          <td>{r.is_safe ? "Passes" : `Fails ${r.n_violations}: ${plainViolation(r.violations[0] ?? "")}`}</td>
                          <td>{o.kind === "plan" && auth ? AUTH[auth.level] : "for comparison"}</td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {pick?.sim && nothing && (
              <>
                <p><b>What {pick.label} buys, against doing nothing:</b></p>
                <ul>
                  <li>Lines live at high fire risk: {nothing.situation.n_high_risk} down to {pick.sim.situation.n_high_risk}.</li>
                  <li>People living downwind of a live high-risk line: {fmtPeople(nothing.situation.people_downwind)} down to {fmtPeople(pick.sim.situation.people_downwind)}.</li>
                  <li>Hospitals and water plants downwind of a live high-risk line: {nothing.fire_exposure.n} down to {pick.sim.fire_exposure.n}.</li>
                  <li>What it costs: {fmtInt(pick.sim.customers_out)} customers without power{pick.sim.buildings_out?.n ? ` and ${buildingsText(pick.sim.buildings_out.by_kind, ", ")} on backup power` : ""}.</li>
                </ul>
                <p className="report-note">A line with no power cannot start a fire, and a targeted shut-off keeps power on for everyone it does not need to cut. Those are the two things this tool is for.</p>
              </>
            )}
          </>
        )}

        <h2>6. Decision</h2>
        {outcome ? <p><b>Plan approved.</b> {outcome}</p> : <p>No plan has been approved for this hour.</p>}
        {forHour.length > 0 && <ol>{forHour.map((e) => <li key={e.n}>{new Date(e.at_utc).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}, {e.role}: {e.text}</li>)}</ol>}
        {bulletin && <pre className="bulletin">{bulletin}</pre>}

        <h2>7. Inputs and how fresh they are</h2>
        <table>
          <thead><tr><th>Input</th><th>Source</th><th>Kind</th></tr></thead>
          <tbody>{inputs.map((r) => <tr key={r.name}><td><b>{r.name}</b></td><td>{r.source}</td><td><span className={`kind kind-${r.kind.toLowerCase()}`}>{r.kind}</span></td></tr>)}</tbody>
        </table>

        <h2>8. What this report cannot see</h2>
        <ul>{LIMITS.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
    </div>
  );
}
