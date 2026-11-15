import type { LAEvent } from "../la/api";
import { inGoTo, scenarioLabel, timeLabel } from "./format";

export type Mode = "operate" | "study";

/** Regions the tool could run on. Only Los Angeles has a grid loaded; the others are listed so the place to switch is clear. */
const REGIONS: [string, string][] = [["la", "Los Angeles"], ["sd", "San Diego"], ["bay", "Bay Area"], ["hou", "Houston"]];

/** The one bar that controls everything: where, when, which scenario, the clock you can scrub, and the mode.
 *  The screen always reads as "now". A scenario replays recorded conditions through the same live view. */
export default function TopBar({ events, eid, t, mode, page, fcBusy, onGoTo, onMode, onRefresh, onPage }: {
  events: LAEvent[]; eid: string; t: string | null; mode: Mode; page: "map" | "evidence"; fcBusy: boolean;
  onGoTo: (id: string) => void; onMode: (m: Mode) => void; onRefresh: () => void; onPage: (p: "map" | "evidence") => void;
}) {
  const ev = events.find((e) => e.id === eid);
  const live = ev?.kind === "forecast";
  return (
    <header className={`topbar ${mode === "study" ? "study" : ""}`}>
      <div className="brand">
        <span className="logo" aria-hidden="true">⚡</span>
        <div className="brand-text">
          <b>Highwire</b>
          <select className="region" aria-label="Region" value="la" onChange={() => undefined}>
            {REGIONS.map(([id, name]) => <option key={id} value={id} disabled={id !== "la"}>{name}{id !== "la" ? " (not loaded)" : " grid"}</option>)}
          </select>
        </div>
      </div>
      {page === "map" ? (
        <>
          <div className="clock">
            <div className="clock-time"><span className={`live-dot ${live ? "live" : "replay"}`} />Now {t ? timeLabel(t, { weekday: "short", hour: "numeric", minute: "2-digit" }) : "…"}</div>
            <div className="clock-sub">
              {live
                ? <>Live forecast from {ev?.fetched_utc ? timeLabel(ev.fetched_utc, { hour: "numeric", minute: "2-digit" }) : "?"} <button className="bar-link" disabled={fcBusy} onClick={onRefresh}>{fcBusy ? "Refreshing…" : "Refresh"}</button></>
                : <>Fed by conditions recorded {t ? timeLabel(t, { month: "short", day: "numeric", year: "numeric" }) : ""}</>}
            </div>
          </div>
          <label className="scenario">
            <span>Scenario</span>
            <select value={eid} onChange={(e) => onGoTo(e.target.value)}>
              {events.filter(inGoTo).map((e) => <option key={e.id} value={e.id}>{scenarioLabel(e)}</option>)}
            </select>
          </label>
          <div className="tl-slot" id="timeline-slot" />
          <div className="segmented" role="tablist" aria-label="Mode">
            <button className={mode === "operate" ? "on" : ""} onClick={() => onMode("operate")}>Operate</button>
            <button className={mode === "study" ? "on study" : ""} onClick={() => onMode("study")}>What-if</button>
          </div>
          <button className="bar-link" onClick={() => onPage("evidence")}>Evidence</button>
        </>
      ) : (
        <><div className="spacer" /><button className="bar-btn" onClick={() => onPage("map")}>Back to the map</button></>
      )}
    </header>
  );
}
