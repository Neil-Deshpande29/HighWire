import { useState } from "react";
import type { BuildingsOut, Plan, Severity } from "../api";
import type { LAStatic } from "../la/api";
import { actionText, buildingsText, fmtInt, hospitalsOf, offLines, plainViolation } from "./format";

const AGENT_NAME: Record<string, string> = {
  fire_agent: "Fire", wind_agent: "Wind", heat_agent: "Heat", brownout_agent: "Brownout", coordinator: "Coordinator", reports_agent: "Reports",
};

const SEVERITY: Record<Severity, { mark: string; word: string }> = {
  high: { mark: "▲", word: "High" }, medium: { mark: "◆", word: "Medium" }, low: { mark: "●", word: "Low" },
};
const SOURCE: Record<string, string> = { osm: "OpenStreetMap", costar: "CoStar" };

/** Warning level as a shape, a word and a colour, so it never depends on colour alone. */
function Sev({ level }: { level: Severity }) {
  return <span className={`sev sev-${level}`}>{SEVERITY[level].mark} {SEVERITY[level].word}</span>;
}

/** What one plan step cuts off: a chip that opens the five buildings that matter most. Code ranks them, not a model. */
function StepWarnings({ built }: { built: BuildingsOut }) {
  const [open, setOpen] = useState(false);
  if (!built.n || !built.severity) return <div className="step-clear">● No listed building loses grid power in this step.</div>;
  return (
    <div className="step-warn">
      <button className={`warn-chip sev-${built.severity}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {SEVERITY[built.severity].mark} {SEVERITY[built.severity].word} · {buildingsText(built.by_kind, ", ")} without grid power
      </button>
      {open && (
        <ul className="warn-list">
          {built.sites.map((b) => (
            <li key={`${b.name}|${b.bus}`}>
              {b.severity && <Sev level={b.severity} />} <b>{b.name}</b>
              <span className="muted"> · {b.kind.replace("_", " ")}{b.backup_hours ? ` · about ${Math.round(b.backup_hours)} h of backup` : " · backup not known"} · {SOURCE[b.source] ?? b.source}</span>
            </li>
          ))}
          {built.n > built.sites.length && <li className="muted">and {built.n - built.sites.length} more, listed in the responder bulletin after approval</li>}
        </ul>
      )}
    </div>
  );
}

export type PlanNums = { highRiskOff: number; customers: number; hospitals: number; ok: boolean; reason: string | null };

export function planNums(p: Plan, s: LAStatic, isHigh: (id: string) => boolean): PlanNums {
  const r = p.result;
  return {
    highRiskOff: [...offLines(p.actions)].filter(isHigh).length, customers: r.customers_out,
    hospitals: r.hospitals_on_backup ?? hospitalsOf(r.critical_on_backup, s), ok: r.is_safe, reason: r.violations[0] ? plainViolation(r.violations[0]) : null,
  };
}

export default function PlanCard({ plan, s, sentence, nums, selected, status, canApprove, approveNote, user, onSelect, onApprove, onReject }: {
  plan: Plan; s: LAStatic; sentence: string; nums: PlanNums; selected: boolean; status?: string; canApprove: boolean; approveNote?: string;
  user?: boolean; onSelect: () => void; onApprove?: () => void; onReject?: () => void;
}) {
  const done = status === "approved" || status === "rejected" || status === "superseded";
  const nViol = plan.result.violations.length;
  const built = plan.result.buildings_out;
  return (
    <div className={`card plan ${selected ? "selected" : ""} ${status ?? ""} ${user ? "user" : ""}`} onClick={onSelect}>
      <div className="plan-top">
        <span className="plan-tag">{user ? "Your plan" : `Plan ${plan.id.replace(/^P/, "")}`}</span>
        {status && status !== "pending" && <span className={`status st-${status}`}>{status}</span>}
      </div>
      <p className="plan-sentence">{sentence}</p>
      <div className="plan-nums">
        <div><b>{nums.highRiskOff}</b><span>high-risk lines turned off</span></div>
        <div><b>{fmtInt(nums.customers)}</b><span>customers losing power</span></div>
        <div className={nums.hospitals ? "warn" : ""}><b>{nums.hospitals}</b><span>hospitals on backup</span></div>
        <div className={nums.ok ? "pass" : "fail"}><b>{nums.ok ? "Passed" : "Failed"}</b><span>physics check</span></div>
      </div>
      {built && built.n > 0 && (
        <div className="plan-buildings">{built.severity && <Sev level={built.severity} />} Without grid power: <b>{buildingsText(built.by_kind)}</b></div>
      )}
      {!nums.ok && <div className="why-not">Can't be approved: {nums.reason}{nViol > 1 ? ` (+${nViol - 1} more)` : ""}.</div>}
      <details className="drawer" onClick={(e) => e.stopPropagation()}>
        <summary>Details</summary>
        <div className="drawer-body">
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
          {built && built.sites.length > 0 && (
            <p className="muted">On backup: {built.sites.map((b) => `${b.name} (${b.kind.replace("_", " ")}${b.backup_hours ? `, about ${Math.round(b.backup_hours)} h` : ""})`).join(" · ")}
              {built.n > built.sites.length ? ` · and ${built.n - built.sites.length} more` : ""}</p>
          )}
          {plan.tradeoffs && <p className="muted">{plan.tradeoffs}</p>}
          {!user && <p className="muted">Confidence {Math.round(plan.confidence * 100)}% · physics attempts {plan.attempts} · {fmtInt(plan.result.unserved_mw)} MW unserved</p>}
          {plan.assumptions.length > 0 && <p className="muted">Assumes: {plan.assumptions.join(" · ")}</p>}
          {plan.result.violations.length > 0 && <ul className="viol">{plan.result.violations.slice(0, 8).map((v) => <li key={v}>{v}</li>)}</ul>}
          {plan.result.watch && plan.result.watch.length > 0 && (
            <>
              <div className="muted">Patrol and monitor ({plan.result.watch.length} large 220-500 kV lines stay on at high risk):</div>
              <ul className="viol">{plan.result.watch.slice(0, 10).map((w) => <li key={w}>{w}</li>)}</ul>
            </>
          )}
        </div>
      </details>
      {selected && !done && (onApprove || onReject) && (
        <div className="plan-btns" onClick={(e) => e.stopPropagation()}>
          {onApprove && <button className="primary" disabled={!canApprove} onClick={onApprove}>Approve</button>}
          {onReject && <button className="secondary" onClick={onReject}>Reject</button>}
          {approveNote && <span className="muted small">{approveNote}</span>}
        </div>
      )}
    </div>
  );
}
