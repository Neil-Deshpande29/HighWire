import type { LAStatic, Role, SimResult } from "../la/api";
import DecisionBar from "./DecisionBar";
import OptionsTable, { OptionDetail, type Option } from "./OptionsTable";

/** The main thing on the screen: the plan the agents worked out, what it buys and costs, and Approve or Reject.
 *  Everything else about the options is one click away underneath. */
export default function PlanPanel({ running, done, options, selected, base, s, heat, peek, stale, staleNote, canAct, busy, nRecord, impact, cost, describe,
  onSelect, onPeek, onAffected, onApprove, onReject, onRecord, onRerun }: {
  running: boolean; done: boolean; options: Option[]; selected: Option | null; base: SimResult | null; s: LAStatic; heat: boolean; peek: boolean;
  stale: boolean; staleNote: string | null; canAct: boolean; busy: boolean; nRecord: number; impact: string | null; cost: string | null;
  describe: (o: Option) => string; onSelect: (key: string | null) => void; onPeek: (on: boolean) => void; onAffected: () => void;
  onApprove: (role: Role) => void; onReject: (reasons: string[], note: string) => void; onRecord: () => void; onRerun: () => void;
}) {
  const plans = options.filter((o) => o.kind === "plan");
  if (running) {
    return (
      <section className="card plan-panel waiting">
        <h2 className="panel-title">The plan</h2>
        <p className="thinking">The agents are working it out<span className="dots"><i /><i /><i /></span></p>
        <p className="muted small">Each agent reads the physics for its own risk, the coordinator builds options, and every option is tested before it reaches you.</p>
      </section>
    );
  }
  if (!done) return null;
  return (
    <section className="card plan-panel">
      <div className="panel-head">
        <h2 className="panel-title">{plans.length ? "The plan" : "Nothing to do"}</h2>
        <button className="chip-link" onClick={onRerun}>Analyse again</button>
      </div>
      {staleNote && <div className="note warn">{staleNote}</div>}
      {!plans.length && <p className="plan-sentence">Every line is within its limits this hour. The agents recommend no switching.</p>}
      {selected && (
        <>
          <OptionDetail option={selected} base={base} s={s} sentence={describe(selected)} heat={heat} peek={peek} onPeek={onPeek} onAffected={onAffected} />
          {impact && selected.kind !== "nothing" && <p className="impact-line">{impact}</p>}
          {cost && selected.kind !== "nothing" && <p className="cost">{cost}</p>}
          <DecisionBar option={selected} study={false} canAct={canAct} busy={busy} nRecord={nRecord} onApprove={onApprove} onReject={onReject} onRecord={onRecord} />
        </>
      )}
      {options.length > 1 && (
        <details className="drawer compare">
          <summary>Compare all {options.length} options</summary>
          <OptionsTable options={options} selected={selected?.key ?? null} heat={heat} stale={stale} onSelect={onSelect} />
        </details>
      )}
    </section>
  );
}
