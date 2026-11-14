import type { LAEvent } from "../la/api";
import { eventLabel, inGoTo, timeLabel } from "./format";

export type Mode = "operate" | "study";

export function Clock({ ev, t, fcBusy, onRefresh }: { ev: LAEvent | undefined; t: string | null; fcBusy: boolean; onRefresh: () => void }) {
  if (!ev) return null;
  const live = ev.kind === "forecast";
  return (
    <div className="clock">
      <div className="clock-time">{t ? timeLabel(t, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "…"}</div>
      <div className={`badge ${live ? "live" : "past"}`}>
        {live ? (<>
          <span className="dot" />Live forecast · updated {ev.fetched_utc ? timeLabel(ev.fetched_utc, { hour: "numeric", minute: "2-digit" }) : "?"} ·{" "}
          <button className="link" disabled={fcBusy} onClick={onRefresh}>{fcBusy ? "Refreshing…" : "Refresh"}</button>
        </>) : <>Recorded weather · {eventLabel(ev)}</>}
      </div>
    </div>
  );
}

export function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div className="segmented" role="tablist">
      <button className={mode === "operate" ? "on" : ""} onClick={() => onChange("operate")}>Operate</button>
      <button className={mode === "study" ? "on study" : ""} onClick={() => onChange("study")}>Study</button>
    </div>
  );
}

export default function TopBar({ events, eid, t, mode, page, fcBusy, onGoTo, onMode, onRefresh, onPage }: {
  events: LAEvent[]; eid: string; t: string | null; mode: Mode; page: "map" | "evidence"; fcBusy: boolean;
  onGoTo: (id: string) => void; onMode: (m: Mode) => void; onRefresh: () => void; onPage: (p: "map" | "evidence") => void;
}) {
  const ev = events.find((e) => e.id === eid);
  const hasNow = events.some((e) => e.id === "FX");
  return (
    <header className={`topbar glass ${mode === "study" ? "study" : ""}`}>
      <div className="brand"><span className="logo">⚡</span>Highwire</div>
      {page === "map" ? (<>
        <Clock ev={ev} t={t} fcBusy={fcBusy} onRefresh={onRefresh} />
        <div className="goto">
          <select value={eid} onChange={(e) => onGoTo(e.target.value)} aria-label="Go to">
            {events.filter(inGoTo).map((e) => <option key={e.id} value={e.id}>{e.id === "FX" ? "Now · live forecast" : `Go to: ${eventLabel(e)}`}</option>)}
          </select>
          {hasNow && eid !== "FX" && <button className="pill" onClick={() => onGoTo("FX")}>Back to now</button>}
        </div>
        <div className="spacer" />
        {mode === "study" && <span className="study-chip">Study mode · nothing here changes the real grid</span>}
        <ModeToggle mode={mode} onChange={onMode} />
        <button className="link" onClick={() => onPage("evidence")}>Evidence</button>
      </>) : (<>
        <div className="spacer" />
        <button className="pill" onClick={() => onPage("map")}>Back to the map</button>
      </>)}
    </header>
  );
}
