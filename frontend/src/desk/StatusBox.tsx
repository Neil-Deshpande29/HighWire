/** How the grid's lines are doing right now, by level. Doubles as the map's key: each colour with its count. */
export type Health = { normal: number; elevated: number; high: number; off: number; total: number; words: [string, string, string] };

/** Inside the "Grid status" box: how many lines are fine, the key to the map's colours, the one number that matters and why. */
export default function StatusBox({ health, metric, label, cause, tone, onReport }: {
  health: Health | null; metric: string; label: string; cause: string; tone: "act" | "ok"; onReport: () => void;
}) {
  const share = (n: number) => (health && health.total ? `${(100 * n) / health.total}%` : "0%");
  return (
    <>
      <div className={`metric ${tone}`}><b>{metric}</b><span>{label}</span></div>
      <p className="cause">{cause}</p>
      {health && (
        <div className="health">
          <div className="health-line"><b>{health.normal.toLocaleString("en-US")}</b> of {health.total.toLocaleString("en-US")} lines are fine</div>
          <div className="health-bar" role="img" aria-label={`${health.normal} normal, ${health.elevated} elevated, ${health.high} high, ${health.off} off`}>
            <i className="k-normal" style={{ width: share(health.normal) }} /><i className="k-elevated" style={{ width: share(health.elevated) }} />
            <i className="k-high" style={{ width: share(health.high) }} /><i className="k-off" style={{ width: share(health.off) }} />
          </div>
          <div className="key">
            <span><i className="k-normal" />{health.words[0]} <b>{health.normal}</b></span>
            <span><i className="k-elevated" />{health.words[1]} <b>{health.elevated}</b></span>
            <span><i className="k-high" />{health.words[2]} <b>{health.high}</b></span>
            {health.off > 0 && <span><i className="k-off" />Off <b>{health.off}</b></span>}
          </div>
        </div>
      )}
      <button className="secondary small-btn" onClick={onReport}>Open the situation report</button>
    </>
  );
}
