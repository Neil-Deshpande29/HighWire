import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Action, Job, Plan } from "../api";
import ChangesTray from "../ui/ChangesTray";
import ConditionsPanel from "../ui/ConditionsPanel";
import {
  degF, fmtInt, fmtPeople, headline, isHeat, mergeAction, mph, offLines, offTargets, planSentence, startIndex, timeLabel, worstHour,
} from "../ui/format";
import LayersMenu, { Legend, type ColorLayer } from "../ui/LayersMenu";
import LineControls from "../ui/LineControls";
import PlanCard, { planNums } from "../ui/PlanCard";
import PlanList, { type AgentChip } from "../ui/PlanList";
import SituationCard, { type BigNumber } from "../ui/SituationCard";
import Timeline from "../ui/Timeline";
import type { Mode } from "../ui/TopBar";
import { la, NO_CONDITIONS, type Conditions, type Hour, type LAEvent, type LAStatic, type LAView, type Series, type SimResult } from "./api";
import LAScene, { C, damageColor, loadColor, riskColor, type Focus, type Layers } from "./LAScene";

type Pop = { kind: "line"; bi: number; x: number; y: number } | { kind: "bus"; id: string; x: number; y: number };
const sameHour = (a?: string | null, b?: string | null) => !!a && !!b && Math.abs(new Date(a).getTime() - new Date(b).getTime()) < 1800e3;
const GRID_CHANGES = new Set(["open_line", "close_line", "limit_line", "shed_load", "deenergize_lines", "deenergize_bus", "dispatch_gen"]);

/** Debounced POST /la/simulate that drops stale answers. */
function useSim(key: string | null, run: () => Promise<SimResult>, delay = 200): [SimResult | null, boolean] {
  const [res, setRes] = useState<SimResult | null>(null);
  const [busy, setBusy] = useState(false);
  const n = useRef(0);
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    if (key == null) { setRes(null); return; }
    const me = ++n.current;
    setBusy(true);
    const h = window.setTimeout(() => {
      runRef.current().then((r) => { if (me === n.current) setRes(r); }).catch(() => null).finally(() => { if (me === n.current) setBusy(false); });
    }, delay);
    return () => window.clearTimeout(h);
  }, [key, delay]);
  return [res, busy];
}

function usePoll(): [Job | null, (id: string) => void, () => void] {
  const [job, setJob] = useState<Job | null>(null);
  const t = useRef<number | null>(null);
  const stop = useCallback(() => { if (t.current) window.clearInterval(t.current); t.current = null; }, []);
  const start = useCallback((id: string) => {
    stop();
    setJob({ status: "running", log: [], output: null });
    t.current = window.setInterval(async () => {
      try { const j = await la.job(id); setJob(j); if (j.status !== "running") stop(); } catch { stop(); }
    }, 900);
  }, [stop]);
  const clear = useCallback(() => { stop(); setJob(null); }, [stop]);
  useEffect(() => stop, [stop]);
  return [job, start, clear];
}

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
  const [layers, setLayers] = useState<Layers>({ wind: false, redflag: true, hospitals: true, labels: true });
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
  const [approvedPlan, setApprovedPlan] = useState<{ off: string[]; t: string; busesOut: string[] | null } | null>(null);
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


  // ---------------------------------------------------------------- clock
  useEffect(() => {
    if (!ev) return;
    setPlaying(false); setRevealed(false); setSeries(null); setHour(null); setSelPlan(null); setSelLine(null); setPop(null);
    setStaged([]); setOutcome(null); setBulletinText(null); setView(null); setApprovedPlan(null); clearOp(); setOpT(null); setStudyActions([]); clearSt();
    setColor(kind === "heat" ? "loading" : "fire");
    pendingFocus.current = true;
    let live = true;
    la.series(eid).then((sr) => { if (live) { setSeries(sr); setI(startIndex(sr, kind)); } }).catch((e) => setErr(String(e)));
    return () => { live = false; };
  }, [eid, ev?.id, version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (series && series.event === eid) la.hour(eid, i).then((h) => { if (h.event === eid) setHour(h); }).catch((e) => setErr(String(e))); }, [eid, i, series]);
  useEffect(() => { onTime(hour?.t_utc ?? null); }, [hour?.t_utc, onTime]);
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
  const plans: Plan[] = useMemo(() => { // the planner can return two plans that switch the same lines: show one
    const seen = new Set<string>();
    return (job?.output?.plans ?? []).filter((p) => {
      const k = JSON.stringify([[...offLines(p.actions)].sort(), p.actions.filter((a) => !offTargets(a).length)]);
      return seen.has(k) ? false : (seen.add(k), true);
    });
  }, [job?.output]);
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
  const colors = useMemo(() => s.branches.map((b, bi) => {
    if (!hour) return C.none;
    if (energized && !energized[bi]) return C.off;
    if (color === "loading") return loadColor(loading[bi]);
    if (b.cable) return C.cable;
    if (color === "storm") return hour.line_damage ? damageColor(hour.line_damage[bi]) : C.none;
    return riskColor(risk[bi], alarm);
  }), [s, hour, energized, color, loading, risk, alarm]);
  const dimBuses = (selPlanObj && previewSim ? previewSim.buses_out : cur ? studySim!.buses_out : !study && staged.length && stagedSim ? stagedSim.buses_out : []) ?? [];

  // ---------------------------------------------------------------- situation
  const sit = study ? studySim?.situation : opSim && sameHour(opSim.t_utc, t) ? opSim.situation : null;
  const tops = (study ? studySim?.top_lines : opSim && sameHour(opSim.t_utc, t) ? opSim.top_lines : null) ?? hour?.over_alarm ?? [];
  const nHigh = study ? sit?.n_high_risk ?? 0 : approved && sit ? sit.n_high_risk : hour?.n_over_alarm ?? 0;
  const isHigh = useCallback((id: string) => { const k = s.branches.findIndex((b) => b.id === id); return k >= 0 && (hour?.lines.risk[k] ?? 0) >= alarm; }, [s, hour, alarm]);
  const labelIds = useMemo(() => {
    if (color === "loading") return loading.map((v, k) => [v, k] as const).filter(([v]) => v >= 80).sort((a, b) => b[0] - a[0]).slice(0, 8).map(([, k]) => s.branches[k].id);
    return tops.slice(0, 8).map((l) => l.id);
  }, [color, loading, tops, s]);

  // camera: frame the risk area when the clock jumps to an event
  useEffect(() => {
    if (!hour || !pendingFocus.current || hour.event !== eid || hour.i !== i || series?.event !== eid) return;
    pendingFocus.current = false;
    const ids = kind === "heat" ? labelIds : hour.over_alarm.map((l) => l.id);
    const pts = ids.map((id) => s.branches.find((b) => b.id === id)?.geometry).filter(Boolean).map((g) => g![Math.floor(g!.length / 2)]);
    const b = s.bbox;
    if (!pts.length) { setFocus({ lat: (b.lat_min + b.lat_max) / 2, lon: (b.lon_min + b.lon_max) / 2, spanKm: 150, key: Date.now() }); return; }
    const med = (v: number[]) => [...v].sort((x, y) => x - y)[Math.floor(v.length / 2)];
    const lat = med(pts.map((p) => p[0])), lon = med(pts.map((p) => p[1]));
    const dist = pts.map((p) => Math.hypot((p[0] - lat) * 110.5, (p[1] - lon) * 92)).sort((x, y) => x - y);
    const span = 2.4 * dist[Math.floor(0.8 * (dist.length - 1))];
    setFocus({ lat, lon, spanKm: Math.min(120, Math.max(65, span)), key: Date.now() });
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

  // ---------------------------------------------------------------- big numbers
  const numbers: BigNumber[] = [];
  if (hour && series) {
    if (heat) {
      numbers.push({ value: String(study ? sit?.n_overloaded ?? "…" : hour.n_overloaded), label: "lines over their rating", tone: (study ? sit?.n_overloaded : hour.n_overloaded) ? "act" : "ok" });
      numbers.push({ value: `${Math.round(study ? sit?.max_loading ?? 0 : hour.max_loading)}%`, label: "busiest line's load", sub: "share of its rating" });
    } else {
      numbers.push({ value: String(nHigh), label: "lines at high fire risk", tone: nHigh ? "act" : "ok" });
      numbers.push({ value: sit ? fmtPeople(sit.people_downwind) : "…", label: "people living downwind", sub: sit?.hospitals_downwind ? `${sit.hospitals_downwind} hospitals` : "within 10 km of them" });
    }
    numbers.push({ value: worst?.text ?? "", label: "worst hour", sub: worst?.sub });
    if (study) numbers.push({ value: studySim ? fmtInt(studySim.customers_out) : "…", label: "customers losing power", sub: "in this study" });
    else if (!past) {
      const k0 = series.now_utc ? Math.max(0, series.t_utc.findIndex((x) => new Date(x) >= new Date(series.now_utc!))) : 0;
      numbers.push({ value: `${(Math.max(...series.load_mw.slice(k0)) / 1000).toFixed(1)} GW`, label: "peak demand ahead", sub: "forecast" });
    } else if (revealed) {
      const o = hour.outages["Los Angeles"];
      numbers.push(o == null ? { value: "n/a", label: "customers out now", sub: "no outage data this hour", tone: "muted" } : { value: fmtInt(o), label: "customers out now", sub: "actual, LA County" });
    }
    else numbers.push({ value: "?", label: "customers out now", tone: "muted", action: "Show what happened", onClick: () => setRevealed(true) });
  }
  const head = hour && worst ? headline(hour, kind, nHigh, tops[0]?.zone ?? null, {
    future: !past && !!series?.now_utc && new Date(hour.t_utc) > new Date(series.now_utc), worst: worst.text,
    regionAct: !study && !heat && hour.region_index >= hour.region_alarm,
    ...(study && sit ? { maxGust: sit.max_gust, minRh: sit.min_rh, maxTemp: sit.max_temp, nOver: sit.n_overloaded, loadMw: sit.load_mw } : {}),
  }) : "Loading the Los Angeles grid…";

  const details = hour && (
    <>
      <p>Strongest gust {study && sit ? sit.max_gust : hour.weather.max_gust} m/s ({mph(study && sit ? sit.max_gust : hour.weather.max_gust)} mph) · lowest humidity {study && sit ? sit.min_rh : hour.weather.min_rh}% · hottest {study && sit ? sit.max_temp : hour.weather.max_temp} °C ({degF(study && sit ? sit.max_temp : hour.weather.max_temp)} °F)</p>
      <p>{hour.redflag_active.length ? `National Weather Service Red Flag Warning in ${hour.redflag_active.length} zone${hour.redflag_active.length === 1 ? "" : "s"} (dashed outline on the map).` : "No Red Flag Warning in effect."}</p>
      {hour.storm && <p>Storm damage: {hour.storm.wind_hazard_pct}% of the network under damaging wind{hour.storm.rain_hazard_pct > 0.5 ? `, ${hour.storm.rain_hazard_pct}% under heavy rain` : ""}. {hour.storm.index >= hour.storm.alarm ? "Outages from downed lines are likely: stage repair crews." : "Widespread wind damage is unlikely."}</p>}
      {tops.length > 0 && (
        <div className="mini-list">
          <div className="eyebrow">Riskiest lines and what is downwind</div>
          {tops.slice(0, 8).map((l) => (
            <button key={l.id} className={`mini ${selLine === l.id ? "on" : ""}`} onClick={() => setSelLine(l.id)}>
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
            <button key={l.id} className="mini" onClick={() => { setSelLine(l.id); setColor("storm"); }}>
              <b>{l.id}</b> <span>{Math.round(l.kv)} kV · {Math.round(l.p_damage * 100)}% chance of damage</span>
            </button>
          ))}
        </div>
      )}
      <p className="muted small">{s.modeled_note}</p>
    </>
  );

  // ---------------------------------------------------------------- plans
  const out = job?.output ?? null;
  const chips: AgentChip[] = [
    { id: "fire_agent", name: "Fire agent", summary: `${nHigh} line${nHigh === 1 ? "" : "s"} flagged` },
    { id: "wind_agent", name: "Wind agent", summary: out?.crew_staging?.above_alarm ? "storm damage likely" : "no storm alarm" },
    { id: "heat_agent", name: "Heat agent", summary: hour?.n_overloaded ? `${hour.n_overloaded} lines hot` : "no overheating" },
    { id: "brownout_agent", name: "Brownout agent", summary: out && out.do_nothing.customers_out ? `${fmtInt(out.do_nothing.customers_out)} at risk` : "no shortfalls" },
  ];
  const status = (id: string) => (!study ? view?.plan_status[id] : undefined);
  const userPlan: Plan | null = !study && staged.length && stagedSim ? {
    id: "YOU", title: "Your plan", actions: staged, steps: staged.map((a) => ({ action: a, why: "", raised_by: [] })),
    result: { is_safe: stagedSim.is_safe, unserved_mw: stagedSim.unserved_mw, customers_out: stagedSim.customers_out, violations: stagedSim.violations,
              critical_on_backup: stagedSim.critical_on_backup, n1_warnings: [], watch: stagedSim.watch },
    tradeoffs: "", confidence: 1, assumptions: [], attempts: 1,
  } : null;
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
          onApprove={study ? undefined : async () => {
            try {
              const r = await la.approve(p.id);
              setView(r.state); setBulletinText(r.bulletin.text); setRevealed(true);
              setOutcome(`${planSentence(p, s, lineZone)}${past ? " What actually happened is now on the timeline." : ""}`);
              setSelPlan(null);
              setApprovedPlan({ off: [...offLines(p.actions)], t: jobT!, busesOut: null });
              // which substations the approved plan leaves without power (for the "got it right" check)
              la.simulate(eid, jobT!, p.actions).then((r) => setApprovedPlan((a) => a && { ...a, busesOut: r.buses_out })).catch(() => null);
            } catch (e) { setErr(String(e)); }
          }}
          onReject={study ? undefined : async () => { await la.reject(p.id); setView((v) => v && { ...v, plan_status: { ...v.plan_status, [p.id]: "rejected" } }); }} />
      ))}
      {userPlan && (plans.length > 0 || opJob) && (
        <PlanCard plan={userPlan} s={s} user sentence={planSentence(userPlan, s, lineZone)} nums={planNums(userPlan, s, isHigh)} selected={selPlan === "YOU"}
          canApprove={false} approveNote="For comparison: approving your own plan isn't wired up yet." onSelect={() => setSelPlan(null)} />
      )}
    </>
  );

  // ---------------------------------------------------------------- "Did Highwire get it right?" (V5 + replay file; never hard-coded)
  const checks: { text: string; lead?: boolean }[] = [];
  if (past && series) {
    for (const c of series.fire_checks) {
      const name = c.fire.replace(/ Fire$/, "");
      const exact = series.fires.find((f) => f.name === c.fire)?.ignition_utc ?? c.ignition_utc; // V5 rounds to the hour
      const ig = new Date(exact).getTime();
      const before = approvedPlan ? new Date(approvedPlan.t).getTime() <= ig : false;
      const sub = c.substation;
      const which = c.named_in_report ? `named in the incident report for the ${name} fire` : `nearest where the ${name} fire started`;
      if (sub && approvedPlan?.busesOut) {
        const min = Math.round((ig - new Date(approvedPlan.t).getTime()) / 6e4);
        const lead = min < 90 ? `${min} minutes` : `${Math.round(min / 60)} hours`;
        checks.push(approvedPlan.busesOut.includes(sub.id)
          ? { lead: true, text: before
              ? `Your plan cut power to ${sub.name} ${lead} before the ${name} fire started. Investigators traced the fire to a circuit fed from that substation. A wire with no power can't spark.`
              : `Your plan cuts power to ${sub.name}, which fed the circuit that started the ${name} fire, but you approved it after the fire had started.` }
          : { text: `The ${name} fire was traced to a circuit fed from ${sub.name}. Your approved plan left that substation powered.` });
      }
      checks.push({ text: c.first_flag_utc
        ? `The power line ${which} (${c.nearest_line}) was flagged as high fire risk at ${timeLabel(c.first_flag_utc, { weekday: "short", hour: "numeric" })}, ${Math.round(c.hours_before ?? 0)} hours before the fire started.`
        : `The power line ${which} (${c.nearest_line}) was not flagged before the fire started. That's a miss on recorded weather.` });
      if (approvedPlan && !sub) {
        if (approvedPlan.off.includes(c.nearest_line)) checks.push({ text: before ? `Your approved plan had already turned that line off.` : `Your plan turns that line off, but you approved it after the fire had started.` });
        else if (c.kv >= 180) checks.push({ text: `That line is a ${Math.round(c.kv)} kV import line; plans keep those on and send a patrol instead of switching them off.` });
        else checks.push({ text: `Your approved plan did not turn that line off.` });
      }
      checks.push({ text: `At the hour the fire started, that line ranked in the top ${Math.max(1, Math.round(c.top_pct))}% of ${c.n_lines} lines by fire risk.` });
      if (c.caveat?.includes("distribution")) checks.push({ text: `The circuit that sparked is a smaller neighborhood line that public maps don't show, so Highwire works one level up: the lines and substations that feed it.` });
      if (c.split === "train") checks.push({ text: `We tuned the model on this storm, so it doesn't count as a test.` });
    }
    const peak = Math.max(0, ...series.la_out.filter((v): v is number => v != null));
    if (peak > 0) checks.push({ text: `Real outages in LA County peaked at ${fmtInt(peak)} customers (government outage records).` });
  }

  // ---------------------------------------------------------------- line / bus controls
  const offNow = (id: string, bi: number) => (energized ? !energized[bi] : false) || studyActions.concat(staged).some((a) => a.type === "open_line" && a.target === id);
  const stage = (a: Action) => { if (study) setStudyActions((l) => mergeAction(l, a)); else setStaged((l) => mergeAction(l, a)); setPop(null); };
  const hovered = hover && hour ? s.branches[hover.bi] : null;
  const hoverText = (() => {
    if (!hovered || !hover || !hour) return "";
    const k = hover.bi;
    if (energized && !energized[k]) return "Off: no fire risk from this line.";
    if (color === "loading") return `Carrying ${Math.round(loading[k])}% of its rating.`;
    if (color === "storm" && hour.line_damage) return `${Math.round(hour.line_damage[k] * 100)}% chance wind damages it this hour.`;
    const g = cur?.gust[k] ?? hour.lines.gust[k], rh = cur?.rh[k] ?? hour.lines.rh[k];
    if (hovered.cable) return "Underground cable: no wind or fire exposure.";
    const lvl = risk[k] >= alarm ? "High fire risk" : risk[k] >= 0.6 * alarm ? "Elevated fire risk" : "Normal fire risk";
    return `${lvl}: gusts ${mph(g)} mph, humidity ${Math.round(rh)}%.`;
  })();
  const simForBus = study ? studySim : opSim;
  const popLine = pop?.kind === "line" ? s.branches[pop.bi] : null;
  const popBus = pop?.kind === "bus" ? s.buses.find((b) => b.id === pop.id) : null;
  const trayActions = study ? studyActions : staged;

  return (
    <div className={`screen ${study ? "study" : ""}`}>
      <div className="map-full">
        <LAScene s={s} hour={hour} colors={colors} layers={layers} labelIds={labelIds} selected={selLine} dimBuses={dimBuses}
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
        <LayersMenu color={color} layers={layers} hasStorm={!!hour?.line_damage} onColor={setColor} onLayers={setLayers} />
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
          Weather: {past ? "Open-Meteo reanalysis (recorded)" : "Open-Meteo forecast"} · Outages: EAGLE-I · Line ratings are modeled from voltage class.
          The AI only proposes; physics checks every plan and nothing switches without your approval.
        </div>
      </aside>
    </div>
  );
}
