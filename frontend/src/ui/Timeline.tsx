import { useRef } from "react";
import type { Series } from "../la/api";
import { curveOf, isHeat, timeLabel } from "./format";

/** Bottom strip: play/pause, scrubbing and one plain curve (lines at high fire risk, or the busiest line's % of rating).
 * Real ignitions and the "Did Highwire get it right?" box only after the reveal. */
export default function Timeline({ series, kind, i, playing, revealed, canReveal, checks, onSeek, onPlay, onReveal }: {
  series: Series; kind: string; i: number; playing: boolean; revealed: boolean; canReveal: boolean; checks: { text: string; lead?: boolean }[];
  onSeek: (k: number) => void; onPlay: () => void; onReveal: () => void;
}) {
  const heat = isHeat(kind);
  const W = 1000, H = 92, pl = 30, pr = 8, pt = 10, pb = 18;
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
    <div className="timeline glass">
      <div className="tl-controls">
        <button className="play" onClick={onPlay} aria-label={playing ? "Pause" : "Play"}>{playing ? "❚❚" : "▶"}</button>
        <div className="tl-title">
          <b>{heat ? "Busiest power line, % of its rating" : "Power lines at high fire risk"}</b>
          <span className="muted small">{heat ? "dashed line = over its rating" : "each hour of the event"}{nowK >= 0 ? " · right of “now” is the forecast" : ""}</span>
        </div>
        <div className="spacer" />
        {canReveal && !revealed && <button className="pill" onClick={onReveal}>Show what happened</button>}
      </div>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); seekAt(e.clientX); }}
        onPointerMove={(e) => { if (drag.current) seekAt(e.clientX); }}
        onPointerUp={() => { drag.current = false; }}>
        {nowK > 0 && <rect x={x(nowK)} y={pt - 6} width={x(n - 1) - x(nowK)} height={H - pb - pt + 6} fill="#007aff" opacity={0.05} />}
        {days.map((k) => (
          <g key={k}>
            <line x1={x(k)} x2={x(k)} y1={pt} y2={H - pb} stroke="#d1d1d6" strokeWidth={1} />
            <text x={x(k) + 4} y={H - 5} fontSize="10" fill="#8e8e93">{timeLabel(series.t_utc[k], { weekday: "short", month: "short", day: "numeric" })}</text>
          </g>
        ))}
        <text x={2} y={pt + 4} fontSize="10" fill="#8e8e93">{top}</text>
        <text x={2} y={H - pb} fontSize="10" fill="#8e8e93">0</text>
        <path d={area} fill="#ff9500" opacity={0.12} />
        {heat && <line x1={pl} x2={W - pr} y1={y(100)} y2={y(100)} stroke="#ff3b30" strokeDasharray="5 5" strokeWidth={1.2} />}
        <path d={path(without)} fill="none" stroke="#ff7a00" strokeWidth={2.2} />
        {revealed && series.fires.map((f, j) => {
          const k = idxOf(f.ignition_utc);
          return k >= 0 ? (
            <g key={f.id}>
              <line x1={x(k)} x2={x(k)} y1={pt} y2={H - pb} stroke="#ff3b30" strokeWidth={1.5} />
              <text x={x(k) + 4} y={pt + 9 + j * 12} fontSize="10" fill="#ff3b30" fontWeight={600}>{f.name.replace(/ Fire$/, "")} fire starts</text>
            </g>
          ) : null;
        })}
        {nowK >= 0 && (<g><line x1={x(nowK)} x2={x(nowK)} y1={pt - 6} y2={H - pb} stroke="#007aff" strokeDasharray="3 3" /><text x={x(nowK) + 4} y={pt + 2} fontSize="10" fill="#007aff">now</text></g>)}
        <line x1={x(i)} x2={x(i)} y1={pt - 8} y2={H - pb} stroke="#1d1d1f" strokeWidth={2} />
        <circle cx={x(i)} cy={y(without[i])} r={4.5} fill="#fff" stroke="#1d1d1f" strokeWidth={2} />
      </svg>
      {revealed && checks.length > 0 && (
        <div className="reveal">
          <b>Did Highwire get it right?</b>
          <ul>{checks.map((c) => <li key={c.text} className={c.lead ? "lead" : ""}>{c.text}</li>)}</ul>
        </div>
      )}
    </div>
  );
}
