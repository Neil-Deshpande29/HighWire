import type { Action } from "../api";
import type { LAStatic, SimResult } from "../la/api";
import { actionText, fmtInt, plainViolation } from "./format";

export default function ChangesTray({ s, actions, result, loading, study, onRemove, onClear, onCompare, onCopyToPlan }: {
  s: LAStatic; actions: Action[]; result: SimResult | null; loading: boolean; study: boolean;
  onRemove: (k: number) => void; onClear: () => void; onCompare?: () => void; onCopyToPlan?: () => void;
}) {
  if (!actions.length) return null;
  return (
    <div className={`tray glass ${study ? "study" : ""}`}>
      <div className="tray-head">
        <b>{study ? "Study changes" : "Your changes"}</b>
        <span className="muted small">{study ? "applied to the study grid" : "staged, not applied"}</span>
      </div>
      <div className="tray-items">
        {actions.map((a, k) => (
          <span key={k} className="tray-item">{actionText(a, s)}<button className="x" onClick={() => onRemove(k)} aria-label="Remove">×</button></span>
        ))}
      </div>
      <div className={`tray-nums ${loading ? "stale" : ""}`}>
        {result ? (<>
          <span><b>{fmtInt(result.customers_out)}</b> customers losing power</span>
          <span><b>{result.hospitals_on_backup}</b> hospitals on backup</span>
          <span className={result.situation.n_high_risk ? "act" : ""}><b>{result.situation.n_high_risk}</b> high-risk lines still on</span>
          <span className={result.is_safe ? "pass" : "fail"}><b>{result.is_safe ? "Passes" : `${result.n_violations} problem${result.n_violations === 1 ? "" : "s"}`}</b> physics check</span>
        </>) : <span className="muted">Running the physics…</span>}
      </div>
      {result && !result.is_safe && <div className="why-not small">{plainViolation(result.violations[0] ?? "")}</div>}
      <div className="tray-btns">
        <button className="secondary" onClick={onClear}>Clear</button>
        {onCopyToPlan && <button className="secondary" onClick={onCopyToPlan}>Copy to plan</button>}
        {onCompare && <button className="primary" onClick={onCompare}>Compare with Co-Pilot</button>}
      </div>
    </div>
  );
}
