import type { ReactNode } from "react";

export type BigNumber = { value: string; label: string; sub?: string; tone?: "act" | "ok" | "muted"; onClick?: () => void; action?: string };

export default function SituationCard({ headline, numbers, details, onGetPlans, running, canRun, study }: {
  headline: string; numbers: BigNumber[]; details: ReactNode; onGetPlans: () => void; running: boolean; canRun: boolean; study: boolean;
}) {
  return (
    <section className="card situation">
      <div className="eyebrow">{study ? "Study · situation" : "Situation"}</div>
      <h1 className="headline">{headline}</h1>
      <div className="numbers">
        {numbers.slice(0, 4).map((n) => (
          <div key={n.label} className={`num ${n.tone ?? ""}`}>
            <b>{n.value}</b>
            <span>{n.label}</span>
            {n.sub && <small>{n.sub}</small>}
            {n.action && n.onClick && <button className="link small" onClick={n.onClick}>{n.action}</button>}
          </div>
        ))}
      </div>
      <details className="drawer">
        <summary>What's driving this</summary>
        <div className="drawer-body">{details}</div>
      </details>
      <button className="primary" disabled={!canRun || running} onClick={onGetPlans}>
        {running ? "Co-Pilot is working…" : study ? "Get Co-Pilot plans for this study" : "Get Co-Pilot plans"}
      </button>
    </section>
  );
}
