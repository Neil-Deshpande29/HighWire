import { useEffect, useRef, useState } from "react";
import type { Series } from "../la/api";
import { curveOf, isHeat, timeLabel } from "./format";

export const SPEEDS = [1, 2, 4] as const;

/** The clock, as a player bar along the bottom of the screen. Left: what is playing and the time it is at. Centre: back
 *  six hours, play or pause, forward six hours, and a thin scrubber with the hour-by-hour risk drawn faintly behind it.
 *  Right: speed, the reveal of what really happened, and whether this is live or a replay. */
export default function PlayerBar({ series, kind, title, live, i, playing, speed, revealed, canReveal, onSeek, onPlay, onSpeed, onReveal }: {
  series: Series; kind: string; title: string; live: boolean; i: number; playing: boolean; speed: number; revealed: boolean; canReveal: boolean;
  onSeek: (k: number) => void; onPlay: () => void; onSpeed: (s: number) => void; onReveal: () => void;
}) {
  const heat = isHeat(kind);
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(520); // drawn at its real width, so nothing is stretched
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(160, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = 26, mid = 17; // the track sits low, the risk curve rises above it like a waveform
  const n = series.t_utc.length, last = Math.max(n - 1, 1);
  const x = (k: number) => (k / last) * W;
  const curve = curveOf(series, kind);
  const ymax = Math.max(...curve, heat ? 100 : 1) * 1.1;
  const y = (v: number) => mid - (v / ymax) * (mid - 2);
  const wave = `M0,${mid} ${curve.map((v, k) => `L${x(k).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} L${W},${mid} Z`;
  const idxOf = (iso: string) => series.t_utc.findIndex((t) => new Date(t).getTime() >= new Date(iso).getTime());
  const nowK = series.now_utc ? idxOf(series.now_utc) : -1;
  const drag = useRef(false);
  const seekAt = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(n - 1, Math.round(((clientX - r.left) / r.width) * last))));
  };
  const jump = (by: number) => onSeek(Math.max(0, Math.min(n - 1, i + by)));
  // the same clock from the keyboard: arrows move an hour, Page Up and Page Down six hours, Home and End the ends
  const onKey = (e: React.KeyboardEvent) => {
    const step: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -6, PageUp: 6 };
    const to = e.key === "Home" ? 0 : e.key === "End" ? n - 1 : e.key in step ? i + step[e.key] : null;
    if (to == null) return;
    e.preventDefault();
    onSeek(Math.max(0, Math.min(n - 1, to)));
  };
  const short = (k: number) => timeLabel(series.t_utc[k], { weekday: "short", hour: "numeric" });
  return (
    <footer className="player" aria-label="Time">
      <div className="player-now">
        <b title={title}>{title}</b>
        <span>{timeLabel(series.t_utc[i], { weekday: "short", month: "short", day: "numeric", hour: "numeric" })}</span>
      </div>
      <div className="player-mid">
        <div className="player-btns">
          <button className="skip" onClick={() => jump(-6)} disabled={i === 0} aria-label="Back six hours" title="Back six hours">⏮</button>
          <button className="play big" onClick={onPlay} aria-label={playing ? "Pause" : "Play through the scenario"}>{playing ? "❚❚" : "▶"}</button>
          <button className="skip" onClick={() => jump(6)} disabled={i >= n - 1} aria-label="Forward six hours" title="Forward six hours">⏭</button>
        </div>
        <div className="scrub">
          <span className="scrub-end">{short(0)}</span>
          <div className="tl-track" ref={box} role="slider" tabIndex={0} aria-label="Time" aria-valuemin={0} aria-valuemax={n - 1} aria-valuenow={i}
            aria-valuetext={timeLabel(series.t_utc[i], { weekday: "long", hour: "numeric", minute: "2-digit" })} onKeyDown={onKey}
            title={heat ? "The busiest line's share of its rating, hour by hour. Drag to change the time." : "Lines at high fire risk, hour by hour. Drag to change the time."}
            onPointerDown={(e) => { drag.current = true; e.currentTarget.setPointerCapture?.(e.pointerId); seekAt(e.clientX); }}
            onPointerMove={(e) => { if (drag.current) seekAt(e.clientX); }}
            onPointerUp={() => { drag.current = false; }}>
            <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden="true">
              <path d={wave} className="wave" />
              <rect x={0} y={mid} width={W} height={3} rx={1.5} className="rail" />
              <rect x={0} y={mid} width={x(i)} height={3} rx={1.5} className="rail-done" />
              {nowK >= 0 && <line x1={x(nowK)} x2={x(nowK)} y1={2} y2={mid + 6} className="mark-now" />}
              {revealed && series.fires.map((f) => {
                const k = idxOf(f.ignition_utc);
                return k >= 0 ? <g key={f.id}><title>{f.name} started here</title><line x1={x(k)} x2={x(k)} y1={0} y2={mid + 6} className="mark-fire" /></g> : null;
              })}
              <circle cx={x(i)} cy={mid + 1.5} r={6} className="knob" />
            </svg>
          </div>
          <span className="scrub-end">{short(n - 1)}</span>
        </div>
      </div>
      <div className="player-side">
        <div className="segmented small" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => <button key={s} className={speed === s ? "on" : ""} aria-pressed={speed === s} onClick={() => onSpeed(s)}>{s}x</button>)}
        </div>
        {canReveal && !revealed && <button className="bar-btn" onClick={onReveal}>What really happened</button>}
        <span className={`badge-live ${live ? "live" : "replay"}`}><span className={`live-dot ${live ? "live" : "replay"}`} />{live ? "Live" : "Replay"}</span>
      </div>
    </footer>
  );
}
