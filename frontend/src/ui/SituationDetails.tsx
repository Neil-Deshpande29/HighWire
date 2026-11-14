import type { Hour, SimResult, TopLine } from "../la/api";
import { degF, fmtPeople, mph } from "./format";

/** Body of the "What's driving this" drawer: weather extremes, Red Flag status, storm damage, and the lines to look at.
 *  In Study the weather extremes come from the study physics (`sit`), everything else from the recorded hour. */
export default function SituationDetails({ hour, study, sit, tops, selLine, heat, modeledNote, onSelectLine, onSelectStormLine }: {
  hour: Hour; study: boolean; sit: SimResult["situation"] | null | undefined; tops: TopLine[]; selLine: string | null; heat: boolean;
  modeledNote: string; onSelectLine: (id: string) => void; onSelectStormLine: (id: string) => void;
}) {
  const gust = study && sit ? sit.max_gust : hour.weather.max_gust;
  const rh = study && sit ? sit.min_rh : hour.weather.min_rh;
  const temp = study && sit ? sit.max_temp : hour.weather.max_temp;
  const zones = hour.redflag_active.length;
  return (
    <>
      <p>Strongest gust {gust} m/s ({mph(gust)} mph) · lowest humidity {rh}% · hottest {temp} °C ({degF(temp)} °F)</p>
      <p>{zones ? `National Weather Service Red Flag Warning in ${zones} zone${zones === 1 ? "" : "s"} (dashed outline on the map).` : "No Red Flag Warning in effect."}</p>
      {hour.storm && <p>Storm damage: {hour.storm.wind_hazard_pct}% of the network under damaging wind{hour.storm.rain_hazard_pct > 0.5 ? `, ${hour.storm.rain_hazard_pct}% under heavy rain` : ""}. {hour.storm.index >= hour.storm.alarm ? "Outages from downed lines are likely: stage repair crews." : "Widespread wind damage is unlikely."}</p>}
      {tops.length > 0 && (
        <div className="mini-list">
          <div className="eyebrow">Riskiest lines and what is downwind</div>
          {tops.slice(0, 8).map((l) => (
            <button key={l.id} className={`mini ${selLine === l.id ? "on" : ""}`} onClick={() => onSelectLine(l.id)}>
              <b>{l.id}</b> <span>{Math.round(l.kv)} kV · gusts {mph(l.gust)} mph · humidity {Math.round(l.rh)}%</span>
              {l.downwind && <em>{fmtPeople(l.downwind.people)} people{l.downwind.hospitals ? ` · ${l.downwind.hospitals} hospital${l.downwind.hospitals > 1 ? "s" : ""}` : ""} downwind</em>}
            </button>
          ))}
        </div>
      )}
      {!heat && hour.storm && hour.storm.top_lines.length > 0 && (
        <div className="mini-list">
          <div className="eyebrow">Most likely to be knocked down (wind agent)</div>
          {hour.storm.top_lines.slice(0, 5).map((l) => (
            <button key={l.id} className="mini" onClick={() => onSelectStormLine(l.id)}>
              <b>{l.id}</b> <span>{Math.round(l.kv)} kV · {Math.round(l.p_damage * 100)}% chance of damage</span>
            </button>
          ))}
        </div>
      )}
      <p className="muted small">{modeledNote}</p>
    </>
  );
}
