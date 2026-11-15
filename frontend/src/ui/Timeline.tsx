import { useEffect, useRef, useState } from "react";
import type { Series } from "../la/api";
import { curveOf, isHeat, timeLabel } from "./format";

/** The clock, inside the top bar: play or pause, drag to move through the scenario, and one plain curve (lines at high
 *  fire risk, or the busiest line's % of rating). What really happened is drawn only after the reveal. */
export default function Timeline({ series, kind, i, playing, revealed, canReveal, onSeek, onPlay, onReveal }: {
  series: Series; kind: string; i: number; playing: boolean; revealed: boolean; canReveal: boolean;
  onSeek: (k: number) => void; onPlay: () => void; onReveal: () => void;
}) {
  const heat = isHeat(kind);
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(420); // drawn at its real width, so labels are never stretched
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(160, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = 44, pl = 6, pr = 6, pt = 6, pb = 13;
  const n = series.t_utc.length;
  const x = (k: number) => pl + (k / Math.max(n - 1, 1)) * (W - pl - pr);
  const curve = curveOf(series, kind);
  const ymax = Math.max(...curve, heat ? 100 : 1) * 1.12;
  const y = (v: number) => pt + (1 - v / ymax) * (H - pt - pb);
  const path = curve.map((v, k) => `${k === 0 ? "M" : "L"}${x(k).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
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
  return (
    <div className="timeline">
      <button className="play" onClick={onPlay} aria-label={playing ? "Pause" : "Play through the scenario"}>{playing ? "❚❚" : "▶"}</button>
      <div className="tl-track" ref={box} title={heat ? "Busiest line, % of its rating, hour by hour. Drag to change the time." : "Lines at high fire risk, hour by hour. Drag to change the time."}>
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} width={W} height={H}
          onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); seekAt(e.clientX); }}
          onPointerMove={(e) => { if (drag.current) seekAt(e.clientX); }}
          onPointerUp={() => { drag.current = false; }}>
          {days.map((k) => (
            <g key={k}>
              <line x1={x(k)} x2={x(k)} y1={pt} y2={H - pb} stroke="#2a3038" strokeWidth={1} />
              <text x={x(k) + 3} y={H - 3} fontSize="9.5" fill="#7d8894">{timeLabel(series.t_utc[k], { weekday: "short", day: "numeric" })}</text>
            </g>
          ))}
          <path d={`${path} L${x(n - 1)},${H - pb} L${x(0)},${H - pb} Z`} fill="#ff8a1f" opacity={0.18} />
          {heat && <line x1={pl} x2={W - pr} y1={y(100)} y2={y(100)} stroke="#ff4d4f" strokeDasharray="4 4" strokeWidth={1} />}
          <path d={path} fill="none" stroke="#ff8a1f" strokeWidth={1.6} />
          {revealed && series.fires.map((f) => {
            const k = idxOf(f.ignition_utc);
            return k >= 0 ? (
              <g key={f.id}>
                <line x1={x(k)} x2={x(k)} y1={pt - 4} y2={H - pb} stroke="#ff4d4f" strokeWidth={1.5} />
                <text x={x(k) + 3} y={pt + 5} fontSize="9.5" fill="#ff8f90" fontWeight={600}>{f.name.replace(/ Fire$/, "")} fire</text>
              </g>
            ) : null;
          })}
          {nowK >= 0 && <line x1={x(nowK)} x2={x(nowK)} y1={pt - 4} y2={H - pb} stroke="#59c2ff" strokeDasharray="3 3" />}
          <line x1={x(i)} x2={x(i)} y1={0} y2={H - pb} stroke="#fff" strokeWidth={2} />
          <circle cx={x(i)} cy={y(curve[i])} r={4} fill="#000" stroke="#fff" strokeWidth={2} />
        </svg>
      </div>
      {canReveal && !revealed && <button className="bar-btn" onClick={onReveal}>What really happened</button>}
    </div>
  );
}
