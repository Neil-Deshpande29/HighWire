import { useState } from "react";
import type { Action, Severity } from "../api";
import type { Branch, Hour, RiskRow, SimResult, TopLine } from "../la/api";
import { buildingsText, fmtInt, fmtPeople, mph, plainAgentText } from "../ui/format";
import { Sev } from "./marks";
import { Sheet } from "./sheets";

export type LineFinding = { agent: string; issue: string; evidence: string; severity: number };

/** One line, in the right column: what the physics reads on it, the switch, and who is near on both sides of the
 *  decision. Opened by clicking the line on the map or any line name. */
export default function LineSheet({ line, bi, hour, lines, alarm, isOff, capPct, verb, register, top, planNotes, findings, modelText, back, onAction, onShow, onClose }: {
  line: Branch; bi: number; hour: Hour; lines: SimResult["lines"] | null; alarm: number; isOff: boolean; capPct?: number; verb: string;
  register: RiskRow | undefined; top: TopLine | undefined; planNotes: string[]; findings: LineFinding[]; modelText: boolean;
  back: string; onAction: (a: Omit<Action, "target">) => void; onShow: () => void; onClose: () => void;
}) {
  const [pct, setPct] = useState(capPct ?? 70);
  const risk = lines?.risk[bi] ?? hour.lines.risk[bi], loading = lines?.loading[bi] ?? hour.lines.loading[bi];
  const gust = lines?.gust[bi] ?? hour.lines.gust[bi], rh = lines?.rh[bi] ?? hour.lines.rh[bi];
  const level: Severity = risk >= alarm ? "high" : risk >= 0.6 * alarm ? "medium" : "low";
  const word = isOff ? "Off" : line.cable ? "Underground" : level === "high" ? "High fire risk" : level === "medium" ? "Elevated fire risk" : "Normal fire risk";
  const dw = register?.downwind ?? top?.downwind, cut = register ? register.if_cut : top?.if_cut;
  const trees = register?.trees ?? top?.trees, veg = register?.greenery ?? top?.greenery;
  const near = dw ? [dw.people ? `${fmtPeople(dw.people)} people` : null, dw.hospitals ? `${dw.hospitals} hospital${dw.hospitals === 1 ? "" : "s"}` : null,
    dw.water_plants ? `${dw.water_plants} water plant${dw.water_plants === 1 ? "" : "s"}` : null].filter(Boolean).join(", ") : "";
  return (
    <Sheet title={`Line ${line.id}`} back={back} onClose={onClose} sub={`${line.name ? `${line.name} · ` : ""}${Math.round(line.kv)} kV · ${line.length_km} km`}>
      <div className="line-level">
        {isOff || line.cable ? <span className="sev">{word}</span> : <Sev level={level} word={word} />}
        <span className="spacer" />
        <button className="link small" onClick={onShow}>Find on map</button>
      </div>
      <div className="readings">
        <div><b className={level === "high" && !isOff ? "act" : ""}>{isOff ? "0" : risk.toFixed(2)}</b><span>fire risk (high from {alarm.toFixed(2)})</span></div>
        <div><b>{mph(gust)} mph</b><span>wind gusts</span></div>
        <div><b>{Math.round(rh)}%</b><span>humidity</span></div>
        <div><b>{Math.round(loading)}%</b><span>of its capacity in use</span></div>
        {trees ? <div><b>{trees.trees}</b><span>trees near the wires · Vexcel</span></div>
          : veg ? <div><b>{Math.round(veg.share * 100)}%</b><span>green ground near the wires</span></div> : null}
      </div>

      <div className="line-controls">
        <button className="pop-btn" onClick={() => onAction({ type: isOff ? "close_line" : "open_line" })}>{isOff ? "Turn this line on" : "Turn this line off"}</button>
        <div className="pop-row">
          <label>Or limit it to <b>{pct}%</b> of capacity</label>
          <input type="range" min={10} max={100} step={5} value={pct} onChange={(e) => setPct(+e.target.value)} />
          <button className="pop-btn" onClick={() => onAction({ type: "limit_line", pct })}>{pct >= 100 ? "Remove limit" : "Set limit"}</button>
        </div>
        <div className="muted small">{verb}</div>
      </div>

      {(dw || cut) && (
        <div className="two-sides">
          <div><em>If left on</em><span>{near ? `${near} downwind` : "Nobody downwind."}</span></div>
          <div><em>If turned off</em>
            {line.kv >= 180 ? <span>Stays on in every plan: it feeds the whole basin.</span>
              : cut ? <span>{cut.n || cut.customers ? <>{cut.severity && <Sev level={cut.severity} />} {cut.n ? `${buildingsText(cut.by_kind, ", ")} on backup · ` : ""}{fmtInt(cut.customers)} customers cut off</> : "Nobody loses power."}</span>
              : <span className="muted">not worked out</span>}
          </div>
        </div>
      )}

      {(planNotes.length > 0 || findings.length > 0) && (
        <>
          <div className="eyebrow">What the agents say</div>
          <ul className="reasons">
            {planNotes.map((n) => <li key={n}>{n}</li>)}
            {findings.map((f, k) => (
              <li key={k}><b>{f.agent}:</b> {f.issue}{!modelText && f.evidence ? <span className="muted"> · {plainAgentText(f.evidence).replace(/^./, (c) => c.toLowerCase())}</span> : null}</li>
            ))}
          </ul>
        </>
      )}
    </Sheet>
  );
}
