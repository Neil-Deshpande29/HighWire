import { useState } from "react";
import type { Action } from "../api";

/** Click popover for a line (Turn off/on, Limit power) or a substation (Shed load). */
export default function LineControls({ x, y, title, sub, kind, isOff, capPct, maxShed, verb, onAction, onClose }: {
  x: number; y: number; title: string; sub: string; kind: "line" | "bus"; isOff?: boolean; capPct?: number; maxShed?: number;
  verb: string; onAction: (a: Omit<Action, "target">) => void; onClose: () => void;
}) {
  const [pct, setPct] = useState(capPct ?? 70);
  const [mw, setMw] = useState(Math.min(50, Math.round(maxShed ?? 0)));
  return (
    <div className="popover glass" style={{ left: x, top: y }} onClick={(e) => e.stopPropagation()}>
      <div className="pop-head"><b>{title}</b><button className="x" onClick={onClose} aria-label="Close">×</button></div>
      <div className="muted small">{sub}</div>
      {kind === "line" ? (<>
        <button className="pop-btn" onClick={() => onAction({ type: isOff ? "close_line" : "open_line" })}>{isOff ? "Turn on" : "Turn off"}</button>
        <div className="pop-row">
          <label>Limit power to <b>{pct}%</b> of rating</label>
          <input type="range" min={10} max={100} step={5} value={pct} onChange={(e) => setPct(+e.target.value)} />
          <button className="pop-btn" onClick={() => onAction({ type: "limit_line", pct })}>{pct >= 100 ? "Remove limit" : "Set limit"}</button>
        </div>
      </>) : (
        <div className="pop-row">
          {maxShed && maxShed > 0.5 ? (<>
            <label>Shed <b>{mw} MW</b> of {Math.round(maxShed)} MW demand here</label>
            <input type="range" min={0} max={Math.round(maxShed)} step={1} value={mw} onChange={(e) => setMw(+e.target.value)} />
            <button className="pop-btn" onClick={() => onAction({ type: "shed_load", mw })}>{mw ? `Shed ${mw} MW` : "Remove shedding"}</button>
          </>) : <span className="muted small">No customer demand is served from this substation.</span>}
        </div>
      )}
      <div className="muted small">{verb}</div>
    </div>
  );
}
