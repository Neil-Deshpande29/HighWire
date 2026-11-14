import { useState } from "react";
import type { BuildingsOut, Plan } from "../api";
import type { LAStatic, SimResult } from "../la/api";
import { actionText, buildingsText, fmtInt, fmtPeople, offLines, plainViolation } from "../ui/format";
import { Sev, sevMark, sevWord } from "./marks";

/** One thing the operator could do at this hour: nothing, a plan from the agents, or their own changes.
 *  Every option's numbers come from the same physics call (POST /la/simulate), so rows can be compared directly. */
export type Option = {
  key: string; label: string; tag?: string; sim: SimResult | null; plan?: Plan; status?: string; kind: "nothing" | "plan" | "yours";
};

const AGENT_NAME: Record<string, string> = {
  fire_agent: "Fire", wind_agent: "Wind", heat_agent: "Heat", brownout_agent: "Brownout", buildings_agent: "Buildings", coordinator: "Coordinator",
};

export type PlanNums = { highRiskOff: number; customers: number; hospitals: number; ok: boolean; reason: string | null };
export function planNums(p: Plan, isHigh: (id: string) => boolean): PlanNums {
  const r = p.result;
  return {
    highRiskOff: [...offLines(p.actions)].filter(isHigh).length, customers: r.customers_out,
    hospitals: r.hospitals_on_backup ?? r.buildings_out?.hospitals ?? 0, ok: r.is_safe, reason: r.violations[0] ? plainViolation(r.violations[0]) : null,
  };
}

/** The table: one row per option, the same five numbers for each. Click a row to see it on the map. */
export default function OptionsTable({ options, selected, heat, stale, onSelect }: {
  options: Option[]; selected: string | null; heat: boolean; stale: boolean; onSelect: (key: string | null) => void;
}) {
  const cols = heat
    ? ["Lines over rating", "Busiest line", "Customers cut off", "Hospitals, water on backup", "Safe?"]
    : ["Risky lines left on", "People downwind", "Customers cut off", "Hospitals, water on backup", "Safe?"];
  return (
    <div className={`card options ${stale ? "stale" : ""}`}>
      <div className="eyebrow">Your options · click one to see it on the map</div>
      <div className="opt-grid opt-head" aria-hidden="true">
        <span />{cols.map((c) => <span key={c}>{c}</span>)}
      </div>
      {options.map((o) => {
        const r = o.sim, sit = r?.situation;
        const sites = r?.buildings_out?.n ?? 0;
        return (
          <button key={o.key} className={`opt-grid opt-row ${selected === o.key ? "selected" : ""} ${o.kind} ${o.status ?? ""}`}
            aria-pressed={selected === o.key} onClick={() => onSelect(selected === o.key ? null : o.key)}>
            <span className="opt-name">
              <b>{o.label}</b>
              {o.tag && <em className="opt-tag">{o.tag}</em>}
              {o.status && o.status !== "pending" && <em className={`status st-${o.status}`}>{o.status}</em>}
            </span>
            {!r ? <span className="opt-wait">checking…</span> : (
              <>
                <span className={heat ? (sit!.n_overloaded ? "act" : "ok") : sit!.n_high_risk ? "act" : "ok"}>{heat ? sit!.n_overloaded : sit!.n_high_risk}</span>
                <span>{heat ? `${Math.round(sit!.max_loading)}%` : fmtPeople(sit!.people_downwind)}</span>
                <span>{fmtInt(r.customers_out)}</span>
                <span className={sites ? "warn" : ""} title={sites ? buildingsText(r.buildings_out!.by_kind, ", ") : "None"}>{sites}</span>
                <span className={r.is_safe ? "pass" : "fail"} title={r.is_safe ? "Passes every physics check" : r.violations.slice(0, 6).map(plainViolation).join("; ")}>
                  {r.is_safe ? "✓ Yes" : "✕ No"}
                </span>
              </>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** What one plan step cuts off: a chip that opens the buildings that matter most. Code ranks them, not a model. */
export function StepWarnings({ built }: { built: BuildingsOut }) {
  const [open, setOpen] = useState(false);
  if (!built.n || !built.severity) return null;
  return (
    <div className="step-warn">
      <button className={`warn-chip sev-${built.severity}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {sevMark(built.severity)} {sevWord(built.severity)} · {buildingsText(built.by_kind, ", ")} go to backup power
      </button>
      {open && (
        <ul className="warn-list">
          {built.sites.map((b) => (
            <li key={`${b.name}|${b.bus}`}>
              {b.severity && <Sev level={b.severity} />} <b>{b.name}</b>
              <span className="muted"> · {b.backup_hours ? `about ${Math.round(b.backup_hours)} h of backup` : "backup not known"}</span>
            </li>
          ))}
          {built.n > built.sites.length && <li className="muted">and {built.n - built.sites.length} more</li>}
        </ul>
      )}
    </div>
  );
}

type Row = { label: string; before: string; after: string; tone: "gain" | "cost" | "same" };
function delta(label: string, b: number, a: number, fmt: (n: number) => string): Row {
  return { label, before: fmt(b), after: fmt(a), tone: a === b ? "same" : a < b ? "gain" : "cost" };
}

/** Before and after for one option against doing nothing: what it buys next to what it costs, from the same physics. */
export function Tradeoff({ base, sim, heat }: { base: SimResult; sim: SimResult; heat: boolean }) {
  const b = base.situation, a = sim.situation;
  const rows: Row[] = heat
    ? [delta("Lines over their rating", b.n_overloaded, a.n_overloaded, fmtInt), delta("Busiest line", b.max_loading, a.max_loading, (n) => `${Math.round(n)}%`)]
    : [
      delta("Lines at high fire risk", b.n_high_risk, a.n_high_risk, fmtInt),
      delta("People downwind of them", b.people_downwind, a.people_downwind, fmtPeople),
      delta("Hospitals, water plants downwind", base.fire_exposure.n, sim.fire_exposure.n, fmtInt),
    ];
  rows.push(delta("Customers cut off", base.customers_out, sim.customers_out, fmtInt));
  rows.push(delta("Hospitals, water plants on backup", base.buildings_out?.n ?? 0, sim.buildings_out?.n ?? 0, fmtInt));
  return (
    <table className="tradeoff">
      <thead><tr><th /><th>Do nothing</th><th>This option</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label} className={r.tone}>
            <td>{r.label}</td><td>{r.before}</td>
            <td><b>{r.after}</b> {r.tone !== "same" && <span className="tone">{r.tone === "gain" ? "▼ safer" : "▲ cost"}</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The selected option, spelled out: what it does, what it buys and costs, and its steps. */
export function OptionDetail({ option, base, s, sentence, heat, peek, onPeek, onAffected }: {
  option: Option; base: SimResult | null; s: LAStatic; sentence: string; heat: boolean; peek: boolean; onPeek: (on: boolean) => void; onAffected: () => void;
}) {
  const sim = option.sim, plan = option.plan;
  return (
    <div className={`card detail ${option.kind}`}>
      <div className="detail-head">
        <span className="plan-tag">{option.label}</span>
        {option.tag && <em className="opt-tag">{option.tag}</em>}
      </div>
      <p className="plan-sentence">{sentence}</p>
      {!sim ? <div className="muted small">Checking the physics…</div> : option.kind === "nothing" ? (
        <div className="why-not">{sim.is_safe ? "Safe as it is." : `Not safe: ${sim.n_violations} problem${sim.n_violations === 1 ? "" : "s"}, such as ${plainViolation(sim.violations[0] ?? "")}.`}</div>
      ) : (
        <>
          {base && <Tradeoff base={base} sim={sim} heat={heat} />}
          {!sim.is_safe && <div className="why-not">Not safe, so it can't be approved: {plainViolation(sim.violations[0] ?? "")}{sim.n_violations > 1 ? ` (+${sim.n_violations - 1} more)` : ""}.</div>}
          <div className="detail-btns">
            <button className="secondary small-btn" aria-pressed={peek} onClick={() => onPeek(!peek)}>{peek ? "Map shows: now" : "Map shows: this option"}</button>
            {(sim.buildings_out?.n || sim.fire_exposure.n) ? <button className="secondary small-btn" onClick={onAffected}>Who is affected</button> : null}
          </div>
        </>
      )}
      {plan && plan.steps.length > 0 && (
        <details className="drawer">
          <summary>{plan.steps.length === 1 ? "The step, and why" : `The ${plan.steps.length} steps, and why`}</summary>
          <ol className="steps">
            {plan.steps.map((st, i) => (
              <li key={i}>
                <b>{actionText(st.action, s)}</b>
                {st.buildings && <StepWarnings built={st.buildings} />}
                {st.why && <div className="muted">{st.why}</div>}
                {st.raised_by.length > 0 && <div className="tags">{st.raised_by.map((a) => <span key={a} className="tag">{AGENT_NAME[a] ?? a} agent</span>)}</div>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
