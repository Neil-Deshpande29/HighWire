import type { Action } from "../api";
import type { LAStatic, SimResult } from "../la/api";
import { actionText, buildingsText, fmtInt, homesText, plainViolation } from "./format";

/** What you switched by hand, and what it does: who loses power and which other lines pick up the load.
 *  The physics answers at once; the agents are not asked, because nothing here is in doubt. */
export default function ChangesTray({ s, actions, result, loading, study, impact, onShowImpact, onRemove, onClear, onCopyToPlan }: {
  s: LAStatic; actions: Action[]; result: SimResult | null; loading: boolean; study: boolean; impact: string | null; onShowImpact?: () => void;
  onRemove: (k: number) => void; onClear: () => void; onCopyToPlan?: () => void;
}) {
  if (!actions.length) return null;
  return (
    <div className={`tray glass ${study ? "study" : ""}`}>
      <div className="tray-head">
        <b>{study ? "What-if changes" : "Your changes"}</b>
        <span className="muted small">{study ? "applied to the what-if grid" : "tried on the map, not switched"}</span>
      </div>
      <div className="tray-items">
        {actions.map((a, k) => (
          <span key={k} className="tray-item">{actionText(a, s)}<button className="x" onClick={() => onRemove(k)} aria-label="Remove">×</button></span>
        ))}
      </div>
      <div className={`tray-nums ${loading ? "stale" : ""}`}>
        {result ? (<>
          <span><b>{fmtInt(result.customers_out)}</b> customers losing power</span>
          <span title={result.buildings_out?.n ? `${buildingsText(result.buildings_out.by_kind, ", ")}${homesText(result.buildings_out.homes)}` : "None"}>
            <b>{result.buildings_out?.critical ?? result.hospitals_on_backup}</b> critical sites on backup</span>
          {result.buildings_out?.homes ? <span><b>{fmtInt(result.buildings_out.homes)}</b> apartment homes</span> : null}
          <span className={result.situation.n_high_risk ? "act" : ""}><b>{result.situation.n_high_risk}</b> high-risk lines still on</span>
          <span className={result.is_safe ? "pass" : "fail"}><b>{result.is_safe ? "Passes" : `${result.n_violations} problem${result.n_violations === 1 ? "" : "s"}`}</b> physics check</span>
        </>) : <span className="muted">Running the physics…</span>}
      </div>
      {result && impact && <div className="impact-line">{impact} {onShowImpact && <button className="link" onClick={onShowImpact}>Show on map</button>}</div>}
      {result && !result.is_safe && <div className="why-not small">{plainViolation(result.violations[0] ?? "")}</div>}
      <div className="tray-btns">
        <button className="secondary" onClick={onClear}>Clear</button>
        {onCopyToPlan && <button className="secondary" onClick={onCopyToPlan}>Copy to plan</button>}
      </div>
    </div>
  );
}
