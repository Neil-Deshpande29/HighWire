import type { LAStatic, Role, SimResult } from "../la/api";
import DecisionBar from "./DecisionBar";
import OptionsTable, { OptionDetail, type Option } from "./OptionsTable";

/** Inside "The plan" box, directly under the agents: one plain sentence of what the plan does, its four key numbers,
 *  and Approve or Reject. The comparison with doing nothing, the steps and their reasons, the cost and the knock-on
 *  effect on other lines are behind "Details"; the other options behind "Compare". */
export default function PlanPanel({ running, done, options, selected, base, s, heat, peek, study, stale, staleNote, onStale, canAct, busy, nRecord, impact, onShowImpact, cost, costNote, describe,
  onSelect, onPeek, onAffected, onApprove, onReject, onRecord, onRerun }: {
  running: boolean; done: boolean; options: Option[]; selected: Option | null; base: SimResult | null; s: LAStatic; heat: boolean; peek: boolean;
  study: boolean; stale: boolean; staleNote: string | null; onStale?: () => void; canAct: boolean; busy: boolean; nRecord: number; impact: string | null; onShowImpact?: () => void; cost: string | null; costNote: string | null;
  describe: (o: Option) => string; onSelect: (key: string | null) => void; onPeek: (on: boolean) => void; onAffected: () => void;
  onApprove: (role: Role) => void; onReject: (reasons: string[], note: string) => void; onRecord: () => void; onRerun: () => void;
}) {
  const plans = options.filter((o) => o.kind === "plan");
  if (running) {
    return (
      <>
        <p className="thinking">The agents are working it out<span className="dots"><i /><i /><i /></span></p>
        <p className="muted small">Each agent reads the physics for its own risk, the coordinator builds options, and every option is tested before it reaches you.</p>
      </>
    );
  }
  if (!done) return <p className="muted">{study ? "Ask the agents on the left to analyse this what-if." : "No plan yet. Call the agents, or wait for the watch to turn red."}</p>;
  return (
    <>
      {staleNote && <div className="note warn">{staleNote} {onStale && <button className="link" onClick={onStale}>Go to that hour</button>}</div>}
      {!plans.length && <p className="plan-sentence">Every line is within its limits this hour. The agents recommend no switching.</p>}
      {selected && (
        <>
          <OptionDetail option={selected} base={base} s={s} sentence={describe(selected)} heat={heat} peek={peek} onPeek={onPeek} onAffected={onAffected}>
            {impact && <p className="impact-line">{impact} {onShowImpact && <button className="link" onClick={onShowImpact}>Show on map</button>}</p>}
            {cost && <p className="cost">{cost} {costNote && <span className="cost-note">{costNote}</span>}</p>}
          </OptionDetail>
          <DecisionBar option={selected} study={study} canAct={canAct} busy={busy} nRecord={nRecord} onApprove={onApprove} onReject={onReject} onRecord={onRecord} />
        </>
      )}
      {options.length > 1 && (
        <details className="drawer compare">
          <summary>Compare all {options.length} options</summary>
          <OptionsTable options={options} selected={selected?.key ?? null} heat={heat} stale={stale} onSelect={onSelect} />
        </details>
      )}
      <button className="link small rerun" onClick={onRerun}>Have the agents analyse this hour again</button>
    </>
  );
}
