import type { Action, Plan } from "../api";

export const TEAM_COLOR: Record<string, string> = {
  cause: "#fb923c",
  reports: "#a78bfa",
  outcome: "#f87171",
  coordinator: "#60a5fa",
  physics: "#34d399",
  system: "#9ca3af",
};

const AGENT_TEAM: Record<string, string> = {
  heat_agent: "cause", wind_agent: "cause", atmosphere_agent: "cause", water_agent: "cause",
  reports_agent: "reports", brownout_agent: "outcome", fire_agent: "outcome", cascade_agent: "outcome", flood_agent: "outcome",
};

export function actionText(a: Action): string {
  switch (a.type) {
    case "open_line": return `Open line ${a.target}`;
    case "close_line": return `Close line ${a.target}`;
    case "shed_load": return `Shed ${a.mw} MW at ${a.target}`;
    case "dispatch_gen": return `Set ${a.target} to ${a.mw} MW`;
    case "deenergize_bus": return `De-energize substation ${a.target}`;
    case "deenergize_lines": return `De-energize ${(a as Action & { targets?: string[] }).targets?.length ?? 0} lines (${a.target})`;
    default: return `${a.type} ${a.target}`;
  }
}

export default function PlanCard({ plan, status, onApprove, onReject, onHover, busy }: {
  plan: Plan; status: string | undefined; busy: boolean;
  onApprove: () => void; onReject: () => void; onHover: (on: boolean) => void;
}) {
  const r = plan.result;
  const done = status === "approved" || status === "rejected" || status === "superseded";
  return (
    <div className={`card plan ${r.is_safe ? "" : "unsafe"} ${status ?? ""}`} onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}>
      {!r.is_safe && <div className="banner">NOT SAFE{plan.least_bad ? ": least-bad option, no safe plan found" : ""}</div>}
      <div className="plan-head">
        <span className="pid">{plan.id}</span>
        <span className="ptitle">{plan.title}</span>
        {status && status !== "pending" && <span className={`tag st-${status}`}>{status}</span>}
      </div>
      <ol className="steps">
        {plan.steps.map((s, i) => (
          <li key={i}>
            <b>{actionText(s.action)}</b>
            <div className="why">{s.why}</div>
            <div className="tags">
              {s.raised_by.map((a) => (
                <span key={a} className="tag" style={{ borderColor: TEAM_COLOR[AGENT_TEAM[a] ?? "system"], color: TEAM_COLOR[AGENT_TEAM[a] ?? "system"] }}>{a}</span>
              ))}
            </div>
          </li>
        ))}
      </ol>
      <div className="result-row">
        <span className={`badge ${r.is_safe ? "ok" : "bad"}`}>{r.is_safe ? "✓ physics: safe" : "✗ physics: unsafe"}</span>
        <span>{r.unserved_mw.toFixed(0)} MW unserved</span>
        <span>{r.customers_out.toLocaleString()} customers out</span>
      </div>
      {r.violations.length > 0 && <ul className="viol">{r.violations.map((v) => <li key={v}>{v}</li>)}</ul>}
      <div className="tradeoffs">{plan.tradeoffs}</div>
      <div className="conf">
        <span>Confidence</span>
        <div className="bar"><div style={{ width: `${Math.round(plan.confidence * 100)}%` }} /></div>
        <span>{Math.round(plan.confidence * 100)}%</span>
      </div>
      {plan.assumptions.length > 0 && <div className="assume">Assumes: {plan.assumptions.join(" · ")}</div>}
      <div className="muted small">Physics attempts: {plan.attempts} · numbers re-verified by the physics engine</div>
      <div className="btns">
        <button className="approve" disabled={!r.is_safe || done || busy} onClick={onApprove}>Approve</button>
        <button className="reject" disabled={done || busy} onClick={onReject}>Reject</button>
      </div>
    </div>
  );
}
