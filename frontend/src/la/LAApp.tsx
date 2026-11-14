import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action, Plan } from "../api";
import ChangesTray from "../ui/ChangesTray";
import ConditionsPanel from "../ui/ConditionsPanel";
import { headline, isHeat, mergeAction, offLines, planSentence, startIndex, timeLabel, worstHour } from "../ui/format";
import LayersMenu, { Legend, type ColorLayer } from "../ui/LayersMenu";
import LineControls from "../ui/LineControls";
import LineSearch from "../ui/LineSearch";
import PlanCard, { planNums } from "../ui/PlanCard";
import PlanList from "../ui/PlanList";
import SituationCard from "../ui/SituationCard";
import SituationDetails from "../ui/SituationDetails";
import Timeline from "../ui/Timeline";
import type { Mode } from "../ui/TopBar";
import { imageryUrl, la, NO_CONDITIONS, type Conditions, type Hour, type Imagery, type LAEvent, type LAStatic, type LAView, type Series } from "./api";
import {
  agentChips, distinctPlans, fireChecks, GRID_CHANGES, labelledLines, lineColors, lineHoverText, riskFrame, sameHour, situationNumbers,
  userPlanFrom, type ApprovedPlan,
} from "./derive";
import { usePoll, useSim } from "./hooks";
import LAScene, { type Focus, type Layers } from "./LAScene";

type Pop = { kind: "line"; bi: number; x: number; y: number } | { kind: "bus"; id: string; x: number; y: number };

/** The one screen: holds the state and wires it to the map and the panels. The rules for what is shown live in
 *  derive.ts, the fetching hooks in hooks.ts, and every panel in ../ui. */
export default function LAApp({ s, events, eid, mode, version, onMode, onTime }: {
  s: LAStatic; events: LAEvent[]; eid: string; mode: Mode; version: number; onMode: (m: Mode) => void; onTime: (t: string | null) => void;
}) {
  const ev = events.find((e) => e.id === eid);
  const kind = ev?.kind ?? "wind";
  const study = mode === "study";
  const past = kind !== "forecast";
  const [series, setSeries] = useState<Series | null>(null);
  const [i, setI] = useState(0);
  const [hour, setHour] = useState<Hour | null>(null);
  const [playing, setPlaying] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [color, setColor] = useState<ColorLayer>("fire");
  // read once: a shared link reopens the same event, hour and imagery layer
  const link = useRef(new URLSearchParams(window.location.search));
  const [layers, setLayers] = useState<Layers>({ wind: false, redflag: true, hospitals: true, labels: true, imagery: link.current.get("imagery") === "1" });
  const [imagery, setImagery] = useState<Imagery | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const pendingFocus = useRef(true);
  const [err, setErr] = useState<string | null>(null);
  // Operate
  const [opJob, startOp, clearOp] = usePoll();
  const [opT, setOpT] = useState<string | null>(null);
  const [view, setView] = useState<LAView | null>(null);
  const [staged, setStaged] = useState<Action[]>([]);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [bulletinText, setBulletinText] = useState<string | null>(null);
  const [approvedPlan, setApprovedPlan] = useState<ApprovedPlan | null>(null);
  // Study
  const [studyActions, setStudyActions] = useState<Action[]>([]);
  const [cond, setCond] = useState<Conditions>(NO_CONDITIONS);
  const [stJob, startSt, clearSt] = usePoll();
  const [stT, setStT] = useState<string | null>(null);
  // map interaction
  const [selPlan, setSelPlan] = useState<string | null>(null);
  const [selLine, setSelLine] = useState<string | null>(null);
  const [hover, setHover] = useState<{ bi: number; x: number; y: number } | null>(null);
  const [pop, setPop] = useState<Pop | null>(null);
  const overlay = useRef<HTMLDivElement>(null);

  useEffect(() => { la.imagery().then(setImagery).catch(() => setImagery(null)); }, []); // is there an aerial picture to offer?

  // ---------------------------------------------------------------- clock
  useEffect(() => {
    if (!ev) return;
    setPlaying(false); setRevealed(false); setSeries(null); setHour(null); setSelPlan(null); setSelLine(null); setPop(null);
    setStaged([]); setOutcome(null); setBulletinText(null); setView(null); setApprovedPlan(null); clearOp(); setOpT(null); setStudyActions([]); clearSt();
    setColor(kind === "heat" ? "loading" : "fire");
    pendingFocus.current = true;
    let live = true;
    la.series(eid).then((sr) => {
      if (!live) return;
      const h = Number(link.current.get("hour"));
      const linked = link.current.get("event") === eid && link.current.has("hour") && Number.isInteger(h) && h >= 0 && h < sr.t_utc.length ? h : null;
      link.current.delete("hour"); // only the first load follows the link; "Go to" picks its own hour as before
      setSeries(sr); setI(linked ?? startIndex(sr, kind));
    }).catch((e) => setErr(String(e)));
    return () => { live = false; };
  }, [eid, ev?.id, version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (series && series.event === eid) la.hour(eid, i).then((h) => { if (h.event === eid) setHour(h); }).catch((e) => setErr(String(e))); }, [eid, i, series]);
  useEffect(() => { onTime(hour?.t_utc ?? null); }, [hour?.t_utc, onTime]);
  useEffect(() => { // keep the address bar a link to what is on screen
    if (!series || series.event !== eid) return;
    const q = new URLSearchParams({ event: eid, hour: String(i) });
    if (layers.imagery) q.set("imagery", "1");
    window.history.replaceState(null, "", `?${q}`);
  }, [eid, i, layers.imagery, series]);
  useEffect(() => {
    if (!playing || !series) return;
    const h = window.setInterval(() => setI((x) => (x + 1 < series.t_utc.length ? x + 1 : (setPlaying(false), x))), 700);
    return () => window.clearInterval(h);
  }, [playing, series]);
  useEffect(() => { // leaving Study discards it
    setStudyActions([]); setCond(NO_CONDITIONS); clearSt(); setStT(null); setSelPlan(null); setPop(null);
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = hour?.t_utc ?? null;
  // Operate state at this moment after an approved plan (if any)
  const hasApproval = (v: LAView | null | undefined) => !!v && Object.values(v.plan_status ?? {}).includes("approved");
  // this session's latest Operate state wins; otherwise what the server already holds for this hour
  const approved = view && sameHour(view.t_utc, t) ? (hasApproval(view) ? view : null) : hasApproval(hour?.copilot) ? hour!.copilot : null;
  const approvedActions: Action[] = useMemo(() => (approved?.opened ?? []).map((id) => ({ type: "open_line", target: id })), [approved]);

  // ---------------------------------------------------------------- physics calls (/la/simulate)
  const [opSim] = useSim(!study && !playing && t ? `${eid}|${t}|${JSON.stringify(approvedActions)}` : null, () => la.simulate(eid, t!, approvedActions), 250);
  const [stagedSim, stagedBusy] = useSim(!study && staged.length && t ? `${eid}|${t}|${JSON.stringify(staged)}|${JSON.stringify(approvedActions)}` : null,
    () => la.simulate(eid, t!, [...approvedActions, ...staged]));
  const studyAll = useMemo(() => [...approvedActions, ...studyActions], [approvedActions, studyActions]);
  const [studySim, studyBusy] = useSim(study && t ? `${eid}|${t}|${JSON.stringify(studyAll)}|${JSON.stringify(cond)}` : null,
    () => la.simulate(eid, t!, studyAll, cond), 150);
  const job = study ? stJob : opJob;
  const jobT = study ? stT : opT;
  const plans: Plan[] = useMemo(() => distinctPlans(job?.output?.plans ?? []), [job?.output]);
  const selPlanObj = plans.find((p) => p.id === selPlan) ?? null;
  const [previewSim] = useSim(selPlanObj && jobT ? `${selPlanObj.id}|${jobT}|${study}` : null,
    () => la.simulate(eid, jobT!, study ? [...studyAll, ...selPlanObj!.actions] : selPlanObj!.actions, study ? cond : undefined), 0);

  // ---------------------------------------------------------------- map colors
  const alarm = hour?.line_alarm ?? s.calibration.fire.line_alarm;
  const cur = study && studySim && sameHour(studySim.t_utc, t) ? studySim.lines : null;
  // map colors follow whatever physics is on screen: plan preview > Study state > staged changes > recorded hour
  const mapSim = selPlanObj && previewSim && sameHour(previewSim.t_utc, t) ? previewSim
    : study ? (studySim && sameHour(studySim.t_utc, t) ? studySim : null)
    : staged.length && stagedSim && sameHour(stagedSim.t_utc, t) ? stagedSim : null;
  const risk = mapSim?.lines.risk ?? hour?.lines.risk ?? [];
  const loading = mapSim?.lines.loading ?? hour?.lines.loading ?? [];
  const energized: boolean[] | null = useMemo(() => {
    if (selPlanObj && previewSim) return previewSim.lines.energized;
    if (cur) return cur.energized;
    if (!study && staged.length && stagedSim) return stagedSim.lines.energized;
    if (approved) return s.branches.map((b) => approved.lines[b.id]?.energized ?? true);
    return null;
  }, [selPlanObj, previewSim, cur, study, staged.length, stagedSim, approved, s]);
  const colors = useMemo(() => lineColors(s, hour, energized, color, loading, risk, alarm), [s, hour, energized, color, loading, risk, alarm]);
  const dimBuses = (selPlanObj && previewSim ? previewSim.buses_out : cur ? studySim!.buses_out : !study && staged.length && stagedSim ? stagedSim.buses_out : []) ?? [];

  // ---------------------------------------------------------------- situation
  const sit = study ? studySim?.situation : opSim && sameHour(opSim.t_utc, t) ? opSim.situation : null;
  const tops = (study ? studySim?.top_lines : opSim && sameHour(opSim.t_utc, t) ? opSim.top_lines : null) ?? hour?.over_alarm ?? [];
  const nHigh = study ? sit?.n_high_risk ?? 0 : approved && sit ? sit.n_high_risk : hour?.n_over_alarm ?? 0;
  const isHigh = useCallback((id: string) => { const k = s.branches.findIndex((b) => b.id === id); return k >= 0 && (hour?.lines.risk[k] ?? 0) >= alarm; }, [s, hour, alarm]);
  const labelIds = useMemo(() => labelledLines(color, loading, tops, s), [color, loading, tops, s]);

  // camera: frame the risk area when the clock jumps to an event
  useEffect(() => {
    if (!hour || !pendingFocus.current || hour.event !== eid || hour.i !== i || series?.event !== eid) return;
    pendingFocus.current = false;
    const ids = kind === "heat" ? labelIds : hour.over_alarm.map((l) => l.id);
    setFocus({ ...riskFrame(s, ids), key: Date.now() });
  }, [hour, kind, labelIds, s, eid, i, series]);

  const runCopilot = useCallback(async () => {
    if (!t) return;
    setErr(null); setPlaying(false); setSelPlan(null);
    try {
      if (study) { const r = await la.run(eid, t, studyAll.length ? studyAll : [], cond); setStT(t); startSt(r.job_id); }
      else { const r = await la.run(eid, t); setView(r.state); setOpT(t); setOutcome(null); startOp(r.job_id); }
    } catch (e) { setErr(String(e)); }
  }, [t, study, eid, studyAll, cond, startSt, startOp]);

  if (!ev) return <div className="loading">Loading…</div>;
  const worst = series ? worstHour(series, kind, i) : null;
  const lineZone = (id: string) => s.branches.find((b) => b.id === id)?.zone;
  const heat = isHeat(kind);

  const numbers = hour && series
    ? situationNumbers({ hour, series, heat, study, past, revealed, sit, nHigh, worst, studySim, onReveal: () => setRevealed(true) })
    : [];
  const head = hour && worst ? headline(hour, kind, nHigh, tops[0]?.zone ?? null, {
    future: !past && !!series?.now_utc && new Date(hour.t_utc) > new Date(series.now_utc), worst: worst.text,
    regionAct: !study && !heat && hour.region_index >= hour.region_alarm,
    ...(study && sit ? { maxGust: sit.max_gust, minRh: sit.min_rh, maxTemp: sit.max_temp, nOver: sit.n_overloaded, loadMw: sit.load_mw } : {}),
  }) : "Loading the Los Angeles grid…";
  const details = hour && (
    <SituationDetails hour={hour} study={study} sit={sit} tops={tops} selLine={selLine} heat={heat} modeledNote={s.modeled_note}
      onSelectLine={setSelLine} onSelectStormLine={(id) => { setSelLine(id); setColor("storm"); }} />
  );

  // ---------------------------------------------------------------- plans
  const out = job?.output ?? null;
  const chips = agentChips(nHigh, out, hour);
  const status = (id: string) => (!study ? view?.plan_status[id] : undefined);
  const userPlan: Plan | null = !study && staged.length && stagedSim ? userPlanFrom(staged, stagedSim) : null;
  const approvePlan = async (p: Plan) => {
    try {
      const r = await la.approve(p.id);
      setView(r.state); setBulletinText(r.bulletin.text); setRevealed(true);
      setOutcome(`${planSentence(p, s, lineZone)}${past ? " What actually happened is now on the timeline." : ""}`);
      setSelPlan(null);
      setApprovedPlan({ off: [...offLines(p.actions)], t: jobT!, busesOut: null });
      // which substations the approved plan leaves without power (for the "got it right" check)
      la.simulate(eid, jobT!, p.actions).then((r) => setApprovedPlan((a) => a && { ...a, busesOut: r.buses_out })).catch(() => null);
    } catch (e) { setErr(String(e)); }
  };
  const rejectPlan = async (p: Plan) => {
    await la.reject(p.id);
    setView((v) => v && { ...v, plan_status: { ...v.plan_status, [p.id]: "rejected" } });
  };
  const cards = (
    <>
      {jobT && t && !sameHour(jobT, t) && plans.length > 0 && (
        <div className="note">These plans are for {timeLabel(jobT)}. <button className="link" onClick={() => series && setI(series.t_utc.findIndex((x) => sameHour(x, jobT)))}>Go there</button></div>
      )}
      {plans.map((p) => (
        <PlanCard key={p.id} plan={p} s={s} sentence={planSentence(p, s, lineZone)} nums={planNums(p, s, isHigh)} selected={selPlan === p.id}
          status={status(p.id)} canApprove={!study && p.result.is_safe && sameHour(jobT, t)}
          approveNote={study ? "Study plans can't be approved. Exit Study to act." : !p.result.is_safe ? "Fails the physics check." : !sameHour(jobT, t) ? "Go to the plan's hour to approve." : undefined}
          onSelect={() => setSelPlan(selPlan === p.id ? null : p.id)}
          onApprove={study ? undefined : () => approvePlan(p)}
          onReject={study ? undefined : () => rejectPlan(p)} />
      ))}
      {userPlan && (plans.length > 0 || opJob) && (
        <PlanCard plan={userPlan} s={s} user sentence={planSentence(userPlan, s, lineZone)} nums={planNums(userPlan, s, isHigh)} selected={selPlan === "YOU"}
          canApprove={false} approveNote="For comparison: approving your own plan isn't wired up yet." onSelect={() => setSelPlan(null)} />
      )}
    </>
  );

  // ---------------------------------------------------------------- "Did Highwire get it right?" (V5 + replay file; never hard-coded)
  const checks = fireChecks(past, series, approvedPlan);

  // ---------------------------------------------------------------- line / bus controls
  const offNow = (id: string, bi: number) => (energized ? !energized[bi] : false) || studyActions.concat(staged).some((a) => a.type === "open_line" && a.target === id);
  const stage = (a: Action) => { if (study) setStudyActions((l) => mergeAction(l, a)); else setStaged((l) => mergeAction(l, a)); setPop(null); };
  const hovered = hover && hour ? s.branches[hover.bi] : null;
  const hoverText = hovered && hover && hour ? lineHoverText(hovered, hover.bi, hour, { energized, color, loading, risk, alarm, cur }) : "";
  const selBi = selLine ? s.branches.findIndex((b) => b.id === selLine) : -1;
  const selText = selBi >= 0 && hour
    ? `${selLine}${s.branches[selBi].name ? ` · ${s.branches[selBi].name}` : ""} · ${Math.round(s.branches[selBi].kv)} kV · ${lineHoverText(s.branches[selBi], selBi, hour, { energized, color, loading, risk, alarm, cur })}`
    : null;
  const findLine = (id: string) => { // select it and bring its midpoint into view
    const g = s.branches.find((b) => b.id === id)?.geometry;
    setSelLine(id); setPop(null);
    if (g?.length) { const [lat, lon] = g[Math.floor(g.length / 2)]; setFocus({ lat, lon, spanKm: 40, key: Date.now() }); }
  };
  const simForBus = study ? studySim : opSim;
  const popLine = pop?.kind === "line" ? s.branches[pop.bi] : null;
  const popBus = pop?.kind === "bus" ? s.buses.find((b) => b.id === pop.id) : null;
  const trayActions = study ? studyActions : staged;

  return (
    <div className={`screen ${study ? "study" : ""}`}>
      <div className="map-full">
        <LAScene s={s} hour={hour} colors={colors} layers={layers} imagery={imagery?.available ? imageryUrl(imagery.built_utc ?? "") : null}
          labelIds={labelIds} selected={selLine} dimBuses={dimBuses}
          showFires={past && revealed} focus={focus} overlay={overlay}
          onHoverLine={(bi, x, y) => setHover(bi == null ? null : { bi, x: x!, y: y! })}
          onClickLine={(bi, x, y) => { setSelLine(s.branches[bi].id); setPop({ kind: "line", bi, x, y }); }}
          onClickBus={(id, x, y) => setPop({ kind: "bus", id, x, y })}
          onMiss={() => { setPop(null); setSelLine(null); }} />
        <div className="labels" ref={overlay} />
      </div>
      {study && <div className="study-frame" />}
      <div className="map-ui-left">
        <Legend color={color} anyOff={!!energized?.some((e) => !e)} hospitals={layers.hospitals} />
        <LayersMenu color={color} layers={layers} hasStorm={!!hour?.line_damage} imagerySource={imagery?.available ? imagery.source : null}
          onColor={setColor} onLayers={setLayers} />
        <LineSearch s={s} selected={selText} onFind={findLine} />
        {study && <ConditionsPanel value={cond} onChange={setCond} />}
      </div>
      {hovered && hover && !pop && (
        <div className="hover-tip glass" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <b>{hovered.id}{hovered.name ? ` · ${hovered.name}` : ""}</b>
          <span className="muted">{Math.round(hovered.kv)} kV · {energized && !energized[hover.bi] ? "off" : "on"}</span>
          <span>{hoverText}</span>
          <span className="muted small">Click for controls</span>
        </div>
      )}
      {popLine && pop?.kind === "line" && (
        <LineControls key={popLine.id} x={pop.x} y={pop.y} kind="line" title={`${popLine.id}${popLine.name ? ` · ${popLine.name}` : ""}`}
          sub={`${Math.round(popLine.kv)} kV · ${popLine.length_km} km${popLine.cable ? " · underground" : ""}`}
          isOff={offNow(popLine.id, pop.bi)} capPct={trayActions.find((a) => a.type === "limit_line" && a.target === popLine.id)?.pct}
          verb={study ? "Applies to the study grid right away." : "Adds to Your changes. Nothing is switched until you approve a plan."}
          onAction={(a) => stage({ ...a, target: popLine.id })} onClose={() => setPop(null)} />
      )}
      {popBus && pop && (
        <LineControls key={popBus.id} x={pop.x} y={pop.y} kind="bus" title={popBus.name} sub={`Substation · ${Math.round(popBus.kv)} kV`}
          maxShed={simForBus?.bus_load[popBus.id]} verb={study ? "Applies to the study grid right away." : "Adds to Your changes."}
          onAction={(a) => stage({ ...a, target: popBus.id })} onClose={() => setPop(null)} />
      )}
      <div className="bottom-left">
        <ChangesTray s={s} actions={trayActions} result={study ? studySim : stagedSim} loading={study ? studyBusy : stagedBusy} study={study}
          onRemove={(k) => (study ? setStudyActions : setStaged)((l) => l.filter((_, j) => j !== k))}
          onClear={() => (study ? setStudyActions([]) : setStaged([]))}
          onCompare={study ? undefined : () => { if (!opJob || !sameHour(opT, t)) runCopilot(); }}
          onCopyToPlan={study ? () => { setStaged(studyActions.filter((a) => GRID_CHANGES.has(a.type))); onMode("operate"); } : undefined} />
        {series && <Timeline series={series} kind={kind} i={i} playing={playing} revealed={revealed} canReveal={past} checks={checks}
          onSeek={setI} onPlay={() => setPlaying((p) => !p)} onReveal={() => setRevealed(true)} />}
      </div>
      <aside className="side">
        {err && <div className="error" onClick={() => setErr(null)}>{err}</div>}
        <SituationCard headline={head} numbers={numbers} details={details} onGetPlans={runCopilot} running={job?.status === "running"} canRun={!!hour} study={study} />
        {(job || bulletinText) && (
          <PlanList job={job} chips={chips} cards={cards} outcome={study ? null : outcome} bulletin={study ? null : bulletinText} noSafe={!!out?.no_safe_option} />
        )}
        <div className="foot muted small">
          Weather: {past ? "Open-Meteo reanalysis (recorded)" : "Open-Meteo forecast"} · Outages: EAGLE-I{layers.imagery && imagery?.available ? ` · Imagery: ${imagery.source}` : ""} · Line ratings are modeled from voltage class.
          The AI only proposes; physics checks every plan and nothing switches without your approval.
        </div>
      </aside>
    </div>
  );
}
