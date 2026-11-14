import type { Series } from "./api";

/** "Model vs reality": risk index (or load for heat events) vs actual customers out, one time axis. */
export default function ModelChart({ series, kind, cursor, onSeek }: { series: Series; kind: string; cursor: number; onSeek: (i: number) => void }) {
  const W = 1000, H = 170, pl = 52, pr = 64, pt = 18, pb = 22;
  const n = series.t_utc.length;
  const x = (i: number) => pl + (i / Math.max(n - 1, 1)) * (W - pl - pr);
  const left = kind === "heat" ? series.max_loading : series.risk_index;
  const lmax = Math.max(...left, kind === "heat" ? 100 : series.region_alarm) * 1.08;
  const yl = (v: number) => pt + (1 - v / lmax) * (H - pt - pb);
  const out = series.la_out.map((v) => (v == null ? null : v));
  const omax = Math.max(1, ...out.filter((v): v is number => v != null)) * 1.08;
  const yr = (v: number) => pt + (1 - v / omax) * (H - pt - pb);
  const path = (vals: (number | null)[], y: (v: number) => number) =>
    vals.map((v, i) => (v == null ? "" : `${i === 0 || vals[i - 1] == null ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join(" ");
  const idxOf = (iso: string | null | undefined) => (iso ? series.t_utc.findIndex((t) => new Date(t).getTime() >= new Date(iso.replace(" ", "T")).getTime()) : -1);
  const v4 = series.v4a;
  const ia = idxOf(v4?.first_alarm_utc), is = idxOf(v4?.first_spike_utc);
  const days: number[] = [];
  series.t_utc.forEach((t, i) => { const d = new Date(t); if (d.toLocaleString("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", hour12: false }) === "0") days.push(i); });
  const thr = kind === "heat" ? 100 : series.region_alarm;
  const storm = kind !== "heat" ? series.storm_index : null;
  const ys = (v: number) => pt + (1 - v) * (H - pt - pb);
  return (
    <div className="chart">
      <div className="chart-head">
        <b>Model vs reality</b>
        <span className="key"><i style={{ background: "#ef4444" }} />{kind === "heat" ? "max line loading % (model)" : "risk index: risk-weighted line-km (model)"}</span>
        {storm && <span className="key"><i style={{ background: "#38bdf8" }} />storm-damage index 0-1 (wind agent)</span>}
        <span className="key"><i style={{ background: "#e5e7eb" }} />LA County customers out (EAGLE-I, actual)</span>
        {v4 && kind !== "heat" && (
          <span className="key lead">
            lead {v4.lead_time_h == null ? "n/a" : `${v4.lead_time_h > 0 ? "+" : ""}${v4.lead_time_h} h`} · Spearman {v4.spearman_la.toFixed(2)}
            {v4.exploratory_lead_to_main_surge_h != null && <span className="muted"> · to main surge {v4.exploratory_lead_to_main_surge_h > 0 ? "+" : ""}{v4.exploratory_lead_to_main_surge_h} h (exploratory)</span>}
          </span>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onClick={(e) => {
        const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        const px = ((e.clientX - r.left) / r.width) * W;
        onSeek(Math.round(((px - pl) / (W - pl - pr)) * (n - 1)));
      }}>
        {days.map((i) => (
          <g key={i}>
            <line x1={x(i)} x2={x(i)} y1={pt} y2={H - pb} stroke="#1f2937" />
            <text x={x(i) + 3} y={H - 6} fontSize="10" fill="#94a3b8">
              {new Date(series.t_utc[i]).toLocaleDateString("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric" })}
            </text>
          </g>
        ))}
        <line x1={pl} x2={W - pr} y1={yl(thr)} y2={yl(thr)} stroke="#ef4444" strokeDasharray="4 4" opacity={0.6} />
        <text x={pl + 4} y={yl(thr) - 4} fontSize="10" fill="#fca5a5">{kind === "heat" ? "100% rating" : "calibrated alarm"}</text>
        <path d={path(out, yr)} fill="none" stroke="#e5e7eb" strokeWidth={1.6} />
        <path d={path(left, yl)} fill="none" stroke="#ef4444" strokeWidth={2} />
        {storm && series.storm_alarm != null && (
          <>
            <line x1={pl} x2={W - pr} y1={ys(series.storm_alarm)} y2={ys(series.storm_alarm)} stroke="#38bdf8" strokeDasharray="2 5" opacity={0.7} />
            <text x={W - pr - 92} y={ys(series.storm_alarm) - 4} fontSize="10" fill="#7dd3fc">storm alarm</text>
            <path d={path(storm, ys)} fill="none" stroke="#38bdf8" strokeWidth={1.8} />
          </>
        )}
        {series.fires.map((f, k) => {
          const i = idxOf(f.ignition_utc);
          return i >= 0 ? (
            <g key={f.id}>
              <line x1={x(i)} x2={x(i)} y1={pt} y2={H - pb} stroke="#f97316" />
              <text x={x(i) + 3} y={pt + 10 + k * 12} fontSize="10" fill="#fdba74">🔥 {f.name}</text>
            </g>
          ) : null;
        })}
        {kind !== "heat" && ia >= 0 && <circle cx={x(ia)} cy={yl(series.region_alarm)} r={5} fill="#ef4444" />}
        {kind !== "heat" && is >= 0 && v4 && <circle cx={x(is)} cy={yr(out[is] ?? 0)} r={5} fill="#e5e7eb" />}
        {kind !== "heat" && ia >= 0 && is >= 0 && (
          <line x1={x(ia)} x2={x(is)} y1={H - pb - 6} y2={H - pb - 6} stroke="#60a5fa" strokeWidth={2} markerEnd="url(#arr)" />
        )}
        <defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#60a5fa" /></marker></defs>
        <line x1={x(cursor)} x2={x(cursor)} y1={pt - 6} y2={H - pb} stroke="#60a5fa" strokeWidth={2} />
        <text x={4} y={pt + 4} fontSize="10" fill="#fca5a5">{Math.round(lmax)}</text>
        <text x={W - pr + 4} y={pt + 4} fontSize="10" fill="#e5e7eb">{Math.round(omax).toLocaleString()}</text>
      </svg>
      <div className="muted small">Numbers from {series.source}. Click the chart to jump to that hour.</div>
    </div>
  );
}
