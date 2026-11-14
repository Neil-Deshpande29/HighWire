export type BigNumber = { value: string; label: string; sub?: string; tone?: "act" | "ok" | "muted"; onClick?: () => void; action?: string };
export type SituationLink = { label: string; onClick: () => void; alert?: boolean };

/** The situation in one sentence and four numbers, pinned at the top of the right column, with the one main button. */
export default function SituationCard({ headline, numbers, links, onGetPlans, running, canRun, study, hasPlans }: {
  headline: string; numbers: BigNumber[]; links: SituationLink[]; onGetPlans: () => void; running: boolean; canRun: boolean; study: boolean; hasPlans: boolean;
}) {
  return (
    <section className="card situation">
      <div className="eyebrow">{study ? "Study · what if" : "Right now"}</div>
      <h1 className="headline">{headline}</h1>
      <div className="numbers">
        {numbers.slice(0, 4).map((n) => (
          <div key={n.label} className={`num ${n.tone ?? ""}`}>
            <b>{n.value}</b>
            <span>{n.label}</span>
            {n.sub && <small>{n.sub}</small>}
          </div>
        ))}
      </div>
      <div className="sit-actions">
        <button className="primary" disabled={!canRun || running} onClick={onGetPlans}>
          {running ? "Agents are working…" : hasPlans ? "Get plans again" : "Get plans from the agents"}
        </button>
        {links.map((l) => <button key={l.label} className={`pill ${l.alert ? "alert" : ""}`} onClick={l.onClick}>{l.alert ? "▲ " : ""}{l.label}</button>)}
      </div>
    </section>
  );
}
