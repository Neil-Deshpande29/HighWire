import Tours from "./Tours";
import { useState, type ReactNode } from "react";
import type { Band, RecordEntry, RiskRegister, RiskRow, Sensors, SimResult, Station } from "../la/api";
import { buildingsText, fmtInt, fmtPeople, homesText, kindWord, mph, timeLabel } from "../ui/format";
import { KindBadge, Sev, type Kind } from "./marks";
import type { Option } from "./OptionsTable";

/** One data feed the screen relies on, and what kind of thing it is. */
export type InputRow = { name: string; source: string; kind: Kind };

/** A sheet replaces the plans in the right column. Back names where it returns to; the map and the Approve bar stay. */
export function Sheet({ title, sub, back, onClose, children }: { title: string; sub?: ReactNode; back?: string; onClose?: () => void; children: ReactNode }) {
  if (!onClose) return <div className="sheet bare">{sub && <div className="muted small">{sub}</div>}{children}</div>; // inside a drop-down box: the box has the title
  return (
    <section className="card sheet">
      <div className="sheet-head">
        <button className="link" onClick={onClose}>‹ Back to {back}</button>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </div>
      <h2 className="sheet-title">{title}</h2>
      {sub && <div className="muted small">{sub}</div>}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- sensors and inputs
const FLAG: Record<Station["flag"], { level: "high" | "medium" | "low"; word: string }> = {
  model_low: { level: "high", word: "Windier than the model" }, model_high: { level: "low", word: "Calmer than the model" },
  agrees: { level: "low", word: "Matches" }, no_report: { level: "medium", word: "No report" },
};

/** One sentence on how the weather stations compare with the weather the model used. */
export function sensorsSentence(sn: Sensors): string {
  if (!sn.reporting) return "No weather station reported, so the model's weather is unchecked.";
  if (sn.model_low.length) return `${sn.model_low.length} of ${sn.reporting} weather stations measured stronger gusts than the model used. Risk near them may be higher than shown.`;
  if (sn.drier.length) return `${sn.drier.length} of ${sn.reporting} weather stations measured drier air than the model used. Risk near them may be higher than shown.`;
  return `All ${sn.reporting} weather stations match the model's wind or are calmer. The risk shown is not understated.`;
}

export function InputsSheet({ inputs, sensors, back, onClose }: { inputs: InputRow[]; sensors: Sensors | null | "error"; back?: string; onClose?: () => void }) {
  const sn = sensors && sensors !== "error" ? sensors : null;
  return (
    <Sheet title="Sensors" sub="Real weather stations, checked against the weather the model used." back={back} onClose={onClose}>
      {sensors === "error" && <div className="banner">▲ Station reports could not be loaded.</div>}
      {!sensors && <div className="muted small">Loading station reports…</div>}
      {sn && (
        <>
          <p className={`sheet-lead ${sn.level}`}>{sn.level === "warn" ? "▲ " : "● "}{sensorsSentence(sn)}</p>
          <table className="mini-table">
            <thead><tr><th>Station</th><th>Gusts: measured</th><th>Model</th><th /></tr></thead>
            <tbody>
              {sn.stations.map((st) => (
                <tr key={st.id}>
                  <td><b>{st.name}</b></td>
                  <td>{st.measured?.gust_ms != null ? `${mph(st.measured.gust_ms)} mph` : "none"}</td>
                  <td>{st.model.gust_ms != null ? `${mph(st.model.gust_ms)} mph` : "n/a"}</td>
                  <td><Sev level={FLAG[st.flag].level} word={FLAG[st.flag].word} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted small">{sn.mode === "live" ? "Latest report from each station" : `Reports from the hour ending ${timeLabel(sn.t_utc, { weekday: "short", hour: "numeric", minute: "2-digit" })}`}</div>
        </>
      )}
      <div className="eyebrow">Where every number comes from</div>
      <table className="mini-table inputs">
        <tbody>{inputs.map((r) => <tr key={r.name}><td><b>{r.name}</b></td><td>{r.source}</td><td><KindBadge kind={r.kind} /></td></tr>)}</tbody>
      </table>
    </Sheet>
  );
}

// ---------------------------------------------------------------- risk by line
type SortKey = "likelihood" | "downwind" | "cut";
const people = (r: RiskRow) => [r.downwind.people ? `${fmtPeople(r.downwind.people)} people` : null,
  r.downwind.hospitals ? `${r.downwind.hospitals} hospital${r.downwind.hospitals === 1 ? "" : "s"}` : null,
  r.downwind.water_plants ? `${r.downwind.water_plants} water plant${r.downwind.water_plants === 1 ? "" : "s"}` : null].filter(Boolean).join(", ");

function RiskLine({ r, onLine }: { r: RiskRow; onLine: (id: string) => void }) {
  const near = people(r);
  return (
    <button className="risk-line" onClick={() => onLine(r.id)}>
      <div className="risk-top"><b>{r.id}</b><span className="muted">{Math.round(r.kv)} kV · {r.zone}</span><span className="spacer" /><span className="risk-index">risk {r.index.toFixed(2)}</span></div>
      <div className="risk-row"><em>Why risky</em><span>gusts {mph(r.gust_ms)} mph · {Math.round(r.rh_pct)}% humidity{r.trees ? ` · ${r.trees.trees} trees near the wires` : ""}</span></div>
      <div className="risk-row"><em>If left on</em><span><Sev level={r.exposure_band} /> {near ? `${near} downwind` : "nobody downwind"}</span></div>
      <div className="risk-row"><em>If turned off</em><span>
        {r.class === "bulk" ? "Stays on: it feeds the whole basin"
          : !r.if_cut || (!r.if_cut.n && !r.if_cut.customers) ? "● Nobody loses power"
          : <>{r.if_cut.severity ? <Sev level={r.if_cut.severity} /> : null} {r.if_cut.n ? `${buildingsText(r.if_cut.by_kind, ", ")}${homesText(r.if_cut.homes)} lose grid power · ` : ""}{fmtInt(r.if_cut.customers)} customers cut off</>}
      </span></div>
    </button>
  );
}

export function RiskSheet({ reg, back, onLine, onClose }: { reg: RiskRegister | null | "error"; back?: string; onLine: (id: string) => void; onClose?: () => void }) {
  const [sort, setSort] = useState<SortKey>("likelihood");
  const [all, setAll] = useState(false);
  const r = reg && reg !== "error" ? reg : null;
  const rows = r ? [...r.rows].sort((a, b) => sort === "downwind" ? b.downwind.people - a.downwind.people
    : sort === "cut" ? (b.if_cut?.customers ?? -1) - (a.if_cut?.customers ?? -1) : b.index - a.index) : [];
  const shown = all ? rows : rows.slice(0, 6);
  const bands: Band[] = ["low", "medium", "high"];
  return (
    <Sheet title="Risk by line" sub="How likely each line is to start a fire, and who it would reach." back={back} onClose={onClose}>
      {reg === "error" && <div className="banner">▲ The risk table could not be loaded.</div>}
      {!reg && <div className="muted small">Working it out…</div>}
      {r && (
        <>
          <table className="matrix">
            <thead><tr><th>Lines by risk</th><th>Few people downwind</th><th>A town or water plant</th><th>A hospital or {fmtPeople(r.bands.people_high)}+ people</th></tr></thead>
            <tbody>
              <tr><th>High risk</th>{bands.map((b) => <td key={b} className={`cell-high-${b}`}><b>{r.matrix.high[b].length}</b></td>)}</tr>
              <tr><th>Elevated</th>{bands.map((b) => <td key={b} className={`cell-elev-${b}`}><b>{r.matrix.elevated[b]}</b></td>)}</tr>
            </tbody>
          </table>
          <div className="sort-row">
            <span className="eyebrow">{r.n_high} line{r.n_high === 1 ? "" : "s"} at high risk</span><span className="spacer" />
            <div className="segmented">
              {(["likelihood", "downwind", "cut"] as SortKey[]).map((k) => <button key={k} className={sort === k ? "on" : ""} onClick={() => setSort(k)}>{k === "likelihood" ? "Riskiest" : k === "downwind" ? "Most people" : "Biggest cut"}</button>)}
            </div>
          </div>
          {shown.map((x) => <RiskLine key={x.id} r={x} onLine={onLine} />)}
          {rows.length > 6 && <button className="link small" onClick={() => setAll((a) => !a)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
          <div className="muted small">Downwind = within 10 km in the direction the wind blows. Risk is a 0 to 1 score; {r.alarm.toFixed(2)} and above is high.</div>
        </>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- who is affected
function Sites({ title, n, empty, children }: { title: string; n: number; empty: string; children: ReactNode }) {
  return (
    <div className="sites">
      <div className="eyebrow">{title}</div>
      {n ? <ul className="warn-list">{children}</ul> : <div className="step-clear">● {empty}</div>}
    </div>
  );
}

export function AffectedSheet({ option, back, onLine, onClose }: { option: Option; back?: string; onLine: (id: string) => void; onClose?: () => void }) {
  const sim: SimResult | null = option.sim;
  const out = sim?.buildings_out, exp = sim?.fire_exposure;
  return (
    <Sheet title={`Who is affected by ${option.label}`} back={back} onClose={onClose}>
      {!sim ? <div className="muted small">Checking the physics…</div> : (
        <>
          <Sites title={`Lose grid power (${out?.n ?? 0})${homesText(out?.homes)}`} n={out?.n ?? 0} empty="Nobody. No listed building loses grid power.">
            {out?.sites.slice(0, 12).map((b) => (
              <li key={`${b.name}|${b.bus}`}>{b.severity && <Sev level={b.severity} />} <b>{b.name}</b>
                <span className="muted"> · {kindWord(b.kind)}{b.backup_hours ? ` · about ${Math.round(b.backup_hours)} h of backup` : ""}{b.units ? ` · ${fmtInt(b.units)} homes` : ""}</span></li>
            ))}
            {out && out.n > 12 && <li className="muted">and {out.n - 12} more</li>}
          </Sites>
          <Tours out={out} />
          <Sites title={`Still downwind of a risky line (${exp?.n ?? 0})`} n={exp?.n ?? 0} empty="Nobody. No listed building is downwind of a line left on at high risk.">
            {exp?.sites.slice(0, 12).map((b) => (
              <li key={`${b.name}|${b.line}`}><Sev level={b.severity} /> <b>{b.name}</b>
                <span className="muted"> · {kindWord(b.kind)} · {b.distance_km.toFixed(1)} km from </span><button className="link" onClick={() => onLine(b.line)}>{b.line}</button></li>
            ))}
            {exp && exp.n > 12 && <li className="muted">and {exp.n - 12} more</li>}
          </Sites>
        </>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------- the log of decisions
const KIND_WORD: Record<string, string> = { run: "Asked", plans: "Plans", escalated: "Sent up", approved: "Approved", rejected: "Rejected", bulletin: "Bulletin", report: "Report", reset: "Reset" };

export function RecordSheet({ entries, back, onReset, onClose }: { entries: RecordEntry[] | null; back?: string; onReset: () => void; onClose?: () => void }) {
  const [copied, setCopied] = useState(false);
  const text = (entries ?? []).map((e) => `${e.n}. ${new Date(e.at_utc).toLocaleTimeString("en-US")} [${e.role}] ${e.text}`).join("\n");
  return (
    <Sheet title="Log" sub="Every request, plan and decision, in order." back={back} onClose={onClose}>
      {!entries ? <div className="muted small">Loading…</div> : entries.length === 0 ? <div className="note">Nothing yet. Entries appear when you ask the agents for plans.</div> : (
        <ol className="record">
          {entries.map((e) => (
            <li key={e.n}>
              <div className="record-top"><span className={`status as-is st-${e.kind}`}>{KIND_WORD[e.kind] ?? e.kind}</span>
                <span className="muted small">{new Date(e.at_utc).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })} · {e.role}</span></div>
              <div>{e.text}</div>
            </li>
          ))}
        </ol>
      )}
      <div className="detail-btns">
        <button className="secondary small-btn" disabled={!entries?.length} onClick={() => navigator.clipboard.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); })}>{copied ? "Copied" : "Copy"}</button>
        <button className="secondary small-btn" onClick={() => { if (window.confirm("Start over? Plans, approvals and this log are cleared.")) onReset(); }}>Start over</button>
      </div>
    </Sheet>
  );
}
