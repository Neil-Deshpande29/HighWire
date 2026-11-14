import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action, Job } from "../api";
import PlanCard, { TEAM_COLOR } from "../components/PlanCard";
import { la, type Hour, type LAEvent, type LAStatic, type LAView, type Series } from "./api";
import LAScene from "./LAScene";
import ModelChart from "./ModelChart";
import SitePanel from "./SitePanel";
import BriefPanel from "./BriefPanel";

const KIND_ICON: Record<string, string> = { wind: "💨", heat: "🌡️", control: "🌤️", forecast: "🔮" };

export default function LAApp() {
  const [s, setS] = useState<LAStatic | null>(null);
  const [events, setEvents] = useState<LAEvent[]>([]);
  const [eid, setEid] = useState("E1");
  const [series, setSeries] = useState<Series | null>(null);
  const [i, setI] = useState(0);
  const [hour, setHour] = useState<Hour | null>(null);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [view, setView] = useState<LAView | null>(null);
  const [preview, setPreview] = useState<Set<string> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [layer, setLayer] = useState<"fire" | "storm">("fire");
  const [fcBusy, setFcBusy] = useState(false);
  const refreshForecast = useCallback(async () => {
    setFcBusy(true); setErr(null);
    try {
      await la.refreshForecast();
      for (let k = 0; k < 120; k++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await la.forecastStatus();
        if (st.status === "error") throw new Error(st.error ?? "forecast failed");
        if (st.status === "done") break;
      }
      const ev2 = await la.events();
      setEvents(ev2); setSeries(null); setEid("FX");
      la.series("FX").then((sr) => { setSeries(sr); const k = sr.now_utc ? sr.t_utc.findIndex((t) => new Date(t).getTime() >= new Date(sr.now_utc!).getTime()) : 0; setI(Math.max(0, k)); });
    } catch (e) { setErr(String(e)); } finally { setFcBusy(false); }
  }, []);
  const overlay = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const poll = useRef<number | null>(null);

  useEffect(() => {
    Promise.all([la.static(), la.events()]).then(([st, ev]) => { setS(st); setEvents(ev); }).catch(() => setErr("Can't reach the backend on :8000"));
  }, []);
  const ev = events.find((e) => e.id === eid);

  // event change: load series, start at the hour the risk index first crossed its alarm (or 0)
  useEffect(() => {
    if (!ev) return;
    setPlaying(false); setJob(null); setView(null); setSelected(null);
    la.series(eid).then((sr) => {
      setSeries(sr);
      const fa = sr.now_utc ?? sr.v4a?.first_alarm_utc;
      const k = fa ? sr.t_utc.findIndex((t) => new Date(t).getTime() >= new Date(fa.replace(" ", "T")).getTime()) : 0;
      setI(Math.max(0, k));
    }).catch((e) => setErr(String(e)));
  }, [eid, ev?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (series) la.hour(eid, i).then(setHour).catch((e) => setErr(String(e))); }, [eid, i, series]);

  useEffect(() => {
    if (!playing || !series) return;
    const h = window.setInterval(() => setI((x) => (x + 1 < series.t_utc.length ? x + 1 : (setPlaying(false), x))), 650);
    return () => window.clearInterval(h);
  }, [playing, series]);

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [job?.log.length]);

  const runCopilot = useCallback(async () => {
    if (!hour) return;
    setBusy(true); setErr(null); setPlaying(false);
    try {
      const { job_id, state } = await la.run(eid, hour.t_utc);
      setView(state);
      setJob({ status: "running", log: [], output: null });
      if (poll.current) window.clearInterval(poll.current);
      poll.current = window.setInterval(async () => {
        const j = await la.job(job_id);
        setJob(j);
        if (j.status !== "running") { window.clearInterval(poll.current!); poll.current = null; }
      }, 1000);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }, [eid, hour]);

  const linesOf = (acts: Action[]) => new Set(acts.flatMap((a) => a.type === "open_line" ? [a.target] : a.type === "deenergize_lines" ? ((a as Action & { targets?: string[] }).targets ?? []) : []));
  const kind = ev?.kind ?? "wind";
  const out = job?.output ?? null;
  const tLocal = hour ? new Date(hour.t_utc).toLocaleString("en-US", { timeZone: "America/Los_Angeles", weekday: "short", month: "short", day: "numeric", hour: "numeric" }) : "";
  const riskPct = hour ? Math.min(100, (hour.region_index / hour.region_alarm) * 50) : 0;

  const banner = useMemo(() => {
    if (!hour || !ev) return null;
    const lit = hour.fires.filter((f) => f.ignited);
    const lines: string[] = [];
    if (kind === "heat") {
      lines.push(`Demand ${Math.round((hour.demand.LDWP ?? 0) + (hour.demand.SCE ?? 0)).toLocaleString()} MW (LDWP + SCE); hottest grid point ${hour.weather.max_temp} °C.`);
      lines.push(`${hour.n_overloaded} lines over their rating; worst ${hour.max_loading}% (modeled ratings).`);
    } else {
      lines.push(`Gusts up to ${hour.weather.max_gust} m/s, humidity down to ${hour.weather.min_rh}%.`);
      lines.push(`${hour.n_over_alarm} line${hour.n_over_alarm === 1 ? "" : "s"} above the calibrated fire alarm; region risk index ${Math.round(hour.region_index)} vs alarm ${Math.round(hour.region_alarm)}.`);
      if (hour.storm) lines.push(`Storm-damage index ${hour.storm.index.toFixed(2)} (alarm ${hour.storm.alarm.toFixed(2)}): ${hour.storm.wind_hazard_pct}% of the network under damaging wind${hour.storm.rain_hazard_pct > 0.5 ? `, ${hour.storm.rain_hazard_pct}% under heavy rain` : ""}.`);
      if (hour.redflag_active.length) lines.push(`NWS Red Flag Warning active in ${hour.redflag_active.length} fire-weather zones.`);
    }
    if (lit.length) lines.push(`Burning: ${lit.map((f) => `${f.name} (${f.hours_since_ignition} h)`).join(", ")}.`);
    if (kind === "forecast") {
      const now = series?.now_utc ? new Date(series.now_utc).getTime() : 0;
      const dh = Math.round((new Date(hour.t_utc).getTime() - now) / 3.6e6);
      lines.push(dh >= 0 ? `Forecast for ${dh} h from now (Open-Meteo, issued ${series?.now_utc?.slice(0, 13).replace("T", " ")} UTC).` : `${-dh} h ago (recent weather).`);
    } else lines.push(`Actually out right now (EAGLE-I): ${(hour.outages["Los Angeles"] ?? 0).toLocaleString()} customers in LA County.`);
    return lines;
  }, [hour, ev, kind, series]);

  if (!s) return <div className="loading">{err ?? "Loading the Los Angeles grid…"}</div>;
  return (
    <div className="app">
      <div className="scene la">
        <div className="scene3d">
          <LAScene s={s} hour={hour} kind={kind} layer={layer} view={view} preview={preview} selected={selected} onSelect={setSelected} overlay={overlay} />
          <div className="labels" ref={overlay} />
          {ev && hour && (
            <div className={`banner-map ${hour.n_over_alarm > 0 || hour.n_overloaded > 0 ? "alert" : "calm"}`}>
              <div className="bm-title"><span className="bm-icon">{KIND_ICON[ev.kind]}</span>{ev.id}: {ev.name}</div>
              <div className="bm-desc">{tLocal} (Los Angeles time) · {ev.kind === "forecast"
                ? <><span className="split test">live</span> prediction from the current weather forecast</>
                : <><span className={`split ${ev.split}`}>{ev.split}</span> event · replaying recorded weather</>}</div>
              <ul className="bm-list">{banner?.map((l) => <li key={l} style={{ borderColor: "#f97316" }}>{l}</li>)}</ul>
            </div>
          )}
          <div className="legend">
            {kind === "heat" ? (<>
              <span><i style={{ background: "#22c55e" }} />&lt;70% rating</span><span><i style={{ background: "#eab308" }} />70–100%</span><span><i style={{ background: "#ef4444" }} />&gt;100%</span>
            </>) : layer === "storm" ? (<>
              <span><i style={{ background: "#1e3a5f" }} />low damage chance</span><span><i style={{ background: "#38bdf8" }} />25%+</span>
              <span><i style={{ background: "#a78bfa" }} />50%+</span><span><i style={{ background: "#e879f9" }} />75%+ (wind agent)</span>
              <span>➤ wind (gust-scaled)</span>
            </>) : (<>
              <span><i style={{ background: "#22c55e" }} />low fire risk</span><span><i style={{ background: "#eab308" }} />elevated</span>
              <span><i style={{ background: "#f97316" }} />near alarm</span><span><i style={{ background: "#ef4444" }} />above calibrated alarm</span>
              <span><i style={{ background: "#f43f5e" }} />Red Flag zone (NWS)</span><span>➤ wind (gust-scaled)</span>
            </>)}
            <span><i style={{ background: "#64748b" }} />underground cable</span><span><i style={{ background: "#334155" }} />de-energized</span><span><i style={{ background: "#f87171" }} />hospital</span>
            <span className="muted">Line ratings/impedances are modeled from voltage class</span>
            {kind !== "heat" && hour?.line_damage && (
              <span className="layer-toggle">
                <button className={layer === "fire" ? "active" : ""} onClick={() => setLayer("fire")}>🔥 Fire risk</button>
                <button className={layer === "storm" ? "active" : ""} onClick={() => setLayer("storm")}>💨 Storm damage</button>
              </span>
            )}
          </div>
        </div>
        {series && <ModelChart series={series} kind={kind} cursor={i} onSeek={(k) => setI(Math.max(0, Math.min(series.t_utc.length - 1, k)))} />}
      </div>

      <div className="panel">
        <div className="title-row"><h1>⚡ Highwire</h1><span className="mode">real LA grid · replay</span></div>
        <section>
          <h3>Historical event</h3>
          <div className="events">
            {events.map((e) => (
              <button key={e.id} className={e.id === eid ? "active" : ""} onClick={() => setEid(e.id)} title={e.name}>
                {KIND_ICON[e.kind]} {e.id === "FX" ? "Live" : e.id} <span className={`split ${e.split}`}>{e.id === "FX" ? "forecast" : e.split}</span>
              </button>
            ))}
          </div>
          <div className="small muted">{ev?.name}</div>
          <button className="refresh" disabled={fcBusy} onClick={refreshForecast}>
            {fcBusy ? "Fetching forecast and running the models…" : "🔮 Refresh live forecast"}
          </button>
        </section>
        {series && hour && (
          <section>
            <div className="player">
              <button onClick={() => setI((x) => Math.max(0, x - 1))}>◀</button>
              <button className="play" onClick={() => setPlaying((p) => !p)}>{playing ? "❚❚ Pause" : "▶ Play"}</button>
              <button onClick={() => setI((x) => Math.min(series.t_utc.length - 1, x + 1))}>▶</button>
              <span className="tlocal">{tLocal}</span>
            </div>
            <input type="range" min={0} max={series.t_utc.length - 1} value={i} onChange={(e) => setI(+e.target.value)} className="slider" />
          </section>
        )}
        {hour && (
          <section className="strip">
            {kind === "heat" ? (<>
              <div className={hour.n_overloaded ? "bad" : "ok"}><b>{hour.n_overloaded}</b><span>lines &gt; rating</span></div>
              <div><b>{Math.round((hour.demand.LDWP ?? 0) + (hour.demand.SCE ?? 0)).toLocaleString()}</b><span>MW demand (actual)</span></div>
            </>) : (<>
              <div className={hour.region_index >= hour.region_alarm ? "bad" : "ok"}><b>{Math.round(hour.region_index)}</b><span>risk index (alarm {Math.round(hour.region_alarm)})</span></div>
              <div className={hour.n_over_alarm ? "bad" : "ok"}><b>{hour.n_over_alarm}</b><span>lines over alarm</span></div>
              {hour.storm && <div className={hour.storm.index >= hour.storm.alarm ? "bad" : "ok"}><b>{hour.storm.index.toFixed(2)}</b><span>storm index (alarm {hour.storm.alarm.toFixed(2)})</span></div>}
            </>)}
            {kind === "forecast"
              ? <div><b>{Math.round((hour.demand.LDWP ?? 0) + (hour.demand.SCE ?? 0)).toLocaleString()}</b><span>MW demand (forecast)</span></div>
              : <div><b>{(hour.outages["Los Angeles"] ?? 0).toLocaleString()}</b><span>LA customers out (actual)</span></div>}
            <div><b>{hour.weather.max_gust}</b><span>max gust m/s</span></div>
          </section>
        )}
        {kind !== "heat" && hour && <div className="riskbar"><div style={{ width: `${riskPct}%` }} /><span style={{ left: "50%" }}>alarm</span></div>}
        {err && <div className="error">{err}</div>}

        {hour && hour.over_alarm.length > 0 && (
          <section>
            <h3>Lines above the fire alarm <span className="muted small">(and what is downwind if they spark)</span></h3>
            <div className="toplines">
              {hour.over_alarm.slice(0, 8).map((l) => (
                <div key={l.id} className={"tl" + (selected === l.id ? " sel" : "")} onClick={() => setSelected(l.id)}>
                  <b>{l.id}</b> {Math.round(l.kv)} kV {l.zone} <span className="bad">{l.risk.toFixed(2)}</span> <span className="muted">gust {l.gust} · RH {l.rh}%</span>
                  {l.downwind && <div className="muted small">downwind 10 km: ~{l.downwind.people.toLocaleString()} people{l.downwind.hospitals ? ` · ${l.downwind.hospitals} hospital${l.downwind.hospitals > 1 ? "s" : ""}` : ""}{l.downwind.water_plants ? ` · ${l.downwind.water_plants} water plant${l.downwind.water_plants > 1 ? "s" : ""}` : ""}</div>}
                </div>
              ))}
              {hour.n_over_alarm > 8 && <div className="muted small">+{hour.n_over_alarm - 8} more</div>}
            </div>
            {selected && hour.over_alarm.some((l) => l.id === selected)
              ? <SitePanel line={selected} eid={eid} i={i} />
              : <div className="muted small">Click a line to see the aerial photo and what is on the ground there.</div>}
          </section>
        )}

        {kind !== "heat" && hour?.storm && hour.storm.top_lines.length > 0 && (
          <section>
            <h3>💨 Wind agent: lines most likely to be knocked down</h3>
            <div className="toplines">
              {hour.storm.top_lines.slice(0, 6).map((l) => (
                <div key={l.id} className={"tl" + (selected === l.id ? " sel" : "")} onClick={() => { setSelected(l.id); setLayer("storm"); }}>
                  <b>{l.id}</b> {Math.round(l.kv)} kV {l.zone} <span style={{ color: "#c4b5fd" }}>{Math.round(l.p_damage * 100)}%</span> <span className="muted">gust {l.gust} m/s</span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section>
          <button className="run" disabled={busy || job?.status === "running" || !hour} onClick={runCopilot}>
            {job?.status === "running" ? "Co-Pilot running…" : "▶ Run Co-Pilot at this hour"}
          </button>
          {job && (
            <div className="log" ref={logRef}>
              {job.log.map((e, k) => <div key={k}><span style={{ color: TEAM_COLOR[e.team] ?? "#9ca3af" }}>{e.agent}</span>: {e.message}</div>)}
            </div>
          )}
        </section>
        {out && (
          <section>
            <h2>Situation</h2><p>{out.situation}</p>
            {out.brief && <BriefPanel brief={out.brief} />}
            {out.crew_staging && (
              <div className={out.crew_staging.above_alarm ? "staging alert" : "staging"}>
                <b>💨 Crew pre-staging (wind agent):</b> storm index {out.crew_staging.storm_index.toFixed(2)} vs alarm {out.crew_staging.alarm.toFixed(2)}.{" "}
                {out.crew_staging.above_alarm && out.crew_staging.pre_stage_crews_near.length
                  ? <>Stage restoration crews near {out.crew_staging.pre_stage_crews_near.slice(0, 3).map((c) => `${c.line} (${Math.round(c.p_damage * 100)}%)`).join(", ")}.</>
                  : <>No pre-staging needed yet.</>}
              </div>
            )}
            {out.no_safe_option && <div className="error">No safe option within 3 actions at this hour. The least-bad plan is shown and cannot be approved; this is where a wider PSPS decision sits with the operator.</div>}
            {out.disagreements.length > 0 && (<><h3>Agent disagreements</h3><ul className="dis">{out.disagreements.map((d) => <li key={d}>{d}</li>)}</ul></>)}
            {out.plans.map((p) => (
              <PlanCard key={p.id} plan={p} status={view?.plan_status[p.id]} busy={busy}
                onHover={(on) => setPreview(on ? linesOf(p.actions) : null)}
                onApprove={async () => { try { const r = await la.approve(p.id); setView(r.state); setPreview(null); } catch (e) { setErr(String(e)); } }}
                onReject={async () => { await la.reject(p.id); setView((v) => v && { ...v, plan_status: { ...v.plan_status, [p.id]: "rejected" } }); }} />
            ))}
          </section>
        )}
        {view?.bulletin && (
          <section>
            <h2>Responder bulletin</h2>
            <pre className="bulletin">{view.bulletin.text}</pre>
            <button onClick={() => navigator.clipboard.writeText(view.bulletin!.text)}>Copy</button>
          </section>
        )}
        <div className="muted small foot">{s.modeled_note} Weather: Open-Meteo reanalysis. Outages: EAGLE-I. Every replay number comes from {hour?.source}.</div>
      </div>
    </div>
  );
}
