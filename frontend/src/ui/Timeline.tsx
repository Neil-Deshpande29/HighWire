import { useRef } from "react";
import type { Series } from "../la/api";
import { curveOf, isHeat, timeLabel } from "./format";

/** The clock, at the bottom of the right column: play/pause, scrubbing and one plain curve (lines at high fire risk,
 * or the busiest line's % of rating). Real ignitions are drawn only after the reveal. */
export default function Timeline({ series, kind, i, playing, revealed, canReveal, onSeek, onPlay, onReveal }: {
  series: Series; kind: string; i: number; playing: boolean; revealed: boolean; canReveal: boolean;
  onSeek: (k: number) => void; onPlay: () => void; onReveal: () => void;
}) {
  const heat = isHeat(kind);
  const W = 372, H = 70, pl = 22, pr = 6, pt = 10, pb = 16; // drawn at the column's own width so the labels are not stretched
  const n = series.t_utc.length;
  const x = (k: number) => pl + (k / Math.max(n - 1, 1)) * (W - pl - pr);
  const without = curveOf(series, kind);
  const ymax = Math.max(...without, heat ? 100 : 1) * 1.12;
  const y = (v: number) => pt + (1 - v / ymax) * (H - pt - pb);
  const path = (vals: number[]) => vals.map((v, k) => `${k === 0 ? "M" : "L"}${x(k).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const idxOf = (iso: string) => series.t_utc.findIndex((t) => new Date(t).getTime() >= new Date(iso).getTime());
  const days: number[] = [];
  series.t_utc.forEach((t, k) => { if (timeLabel(t, { hour: "numeric", hour12: false }) === "0") days.push(k); });
  const nowK = series.now_utc ? idxOf(series.now_utc) : -1;
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef(false);
  const seekAt = (clientX: number) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((clientX - r.left) / r.width) * W;
    onSeek(Math.max(0, Math.min(n - 1, Math.round(((px - pl) / (W - pl - pr)) * (n - 1)))));
  };
  const area = `${path(without)} L${x(n - 1)},${H - pb} L${x(0)},${H - pb} Z`;
  const top = heat ? `${Math.round(ymax / 1.12)}%` : `${Math.round(ymax / 1.12)}`;
  return (
    <div className="card timeline">
      <div className="tl-controls">
        <button className="play" onClick={onPlay} aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
        <div className="tl-title">
          <b>{heat ? "Busiest line, % of its rating" : "Lines at high fire risk"}</b>
          <span className="muted small">hour by hour · drag to change the time</span>
        </div>
        <div className="spacer" />
        {canReveal && !revealed && <button className="pill" onClick={onReveal}>What happened?</button>}
      </div>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); seekAt(e.clientX); }}
        onPointerMove={(e) => { if (drag.current) seekAt(e.clientX); }}
        onPointerUp={() => { drag.current = false; }}>
        {nowK > 0 && <rect x={x(nowK)} y={pt - 6} width={x(n - 1) - x(nowK)} height={H - pb - pt + 6} fill="#007aff" opacity={0.05} />}
        {days.map((k) => (
          <g key={k}>
            <line x1={x(k)} x2={x(k)} y1={pt} y2={H - pb} stroke="#d1d1d6" strokeWidth={1} />
            <text x={x(k) + 3} y={H - 4} fontSize="9" fill="#8e8e93">{timeLabel(series.t_utc[k], { weekday: "short", day: "numeric" })}</text>
          </g>
        ))}
        <text x={2} y={pt + 4} fontSize="10" fill="#8e8e93">{top}</text>
        <text x={2} y={H - pb} fontSize="10" fill="#8e8e93">0</text>
        <path d={area} fill="#ff9500" opacity={0.12} />
        {heat && <line x1={pl} x2={W - pr} y1={y(100)} y2={y(100)} stroke="#ff3b30" strokeDasharray="5 5" strokeWidth={1.2} />}
        <path d={path(without)} fill="none" stroke="#ff7a00" strokeWidth={1.8} />
        {revealed && series.fires.map((f, j) => {
          const k = idxOf(f.ignition_utc);
          return k >= 0 ? (
            <g key={f.id}>
              <line x1={x(k)} x2={x(k)} y1={pt} y2={H - pb} stroke="#ff3b30" strokeWidth={1.5} />
              <text x={x(k) + 3} y={pt + 8 + j * 11} fontSize="9" fill="#ff3b30" fontWeight={600}>{f.name.replace(/ Fire$/, "")} fire</text>
            </g>
          ) : null;
        })}
        {nowK >= 0 && (<g><line x1={x(nowK)} x2={x(nowK)} y1={pt - 6} y2={H - pb} stroke="#007aff" strokeDasharray="3 3" /><text x={x(nowK) + 4} y={pt + 2} fontSize="10" fill="#007aff">now</text></g>)}
        <line x1={x(i)} x2={x(i)} y1={pt - 8} y2={H - pb} stroke="#1d1d1f" strokeWidth={2} />
        <circle cx={x(i)} cy={y(without[i])} r={4} fill="#fff" stroke="#1d1d1f" strokeWidth={2} />
      </svg>
    </div>
  );
}
