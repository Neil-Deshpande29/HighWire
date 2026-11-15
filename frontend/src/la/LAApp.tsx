import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Action, BuildingsOut, Plan } from "../api";
import LineSheet, { type LineFinding } from "../desk/LineSheet";
import NavPad, { type NavCmd } from "../desk/NavPad";
import type { Option } from "../desk/OptionsTable";
import PlanPanel from "../desk/PlanPanel";
import Section from "../desk/Section";
import StatusBox from "../desk/StatusBox";
import { AffectedSheet, InputsSheet, RecordSheet, RiskSheet, sensorsSentence, type InputRow } from "../desk/sheets";
import ChangesTray from "../ui/ChangesTray";
import ConditionsPanel from "../ui/ConditionsPanel";
import { eventLabel, headline, isHeat, mergeAction, offLines, planSentence, startIndex, timeLabel, usd, worstHour } from "../ui/format";
import LayersMenu, { type ColorLayer } from "../ui/LayersMenu";
import LineControls from "../ui/LineControls";
import LineSearch from "../ui/LineSearch";
import ReportPage from "../ui/ReportPage";
import TeamPanel, { AgentLog, PriorityLadder } from "../ui/TeamPanel";
import Timeline from "../ui/Timeline";
import type { Mode } from "../ui/TopBar";
import {
  imageryUrl, la, NO_CONDITIONS, surroundUrl, type BuildingRecords, type Conditions, type Hour, type Imagery, type LAEvent, type LAStatic, type LAView,
  type RecordEntry, type RiskRegister, type Role, type Sensors, type Series,
} from "./api";
import {
  agentChips, distinctPlans, fireChecks, GRID_CHANGES, labelledLines, lineColors, lineHealth, lineHoverText, lineImpact, riskFrame, sameHour,
  situationNumbers, userPlanFrom, type ApprovedPlan,
} from "./derive";
import { useBackendOnline, usePoll, useSim, useSims } from "./hooks";
import LAScene, { type Focus, type Layers, type Nav } from "./LAScene";

const NO_ACTIONS: Action[] = [];
// the pop-up at a map click: a line (what it reads, the switch) or a substation (shed load)
type Pop = { kind: "line"; id: string; x: number; y: number } | { kind: "bus"; id: string; x: number; y: number };
const AGENT_WORD: Record<string, string> = { fire_agent: "Fire agent", wind_agent: "Wind agent", heat_agent: "Heat agent", brownout_agent: "Brownout agent", buildings_agent: "Buildings agent" };
const STALE_AFTER_H = 3; // a live forecast older than this gets a warning banner

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
  const [layers, setLayers] = useState<Layers>({ wind: false, redflag: true, hospitals: true, labels: true, imagery: link.current.get("imagery") !== "0" });
  const [imagery, setImagery] = useState<Imagery | null>(null);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [nav, setNav] = useState<Nav | null>(null);
  const [slot, setSlot] = useState<HTMLElement | null>(null); // where the top bar wants the timeline drawn
  useEffect(() => { setSlot(document.getElementById("timeline-slot")); }, []);
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
  const online = useBackendOnline();
  const [showReport, setShowReport] = useState(false);
  // the desk: who is acting, which sheet is open, the sensor check, the risk register, the decision record
  const [records, setRecords] = useState<BuildingRecords | null>(null); // which building records are loaded, and the priority ladder
  const [cutOf, setCutOf] = useState<{ key: string; data: BuildingsOut & { customers: number } } | null>(null);
  // every box in the two docks is a drop-down: which ones are open. The plan and the agent team start open.
  const [boxes, setBoxes] = useState<Record<string, boolean>>({ plan: true, team: true });
  const isOpen = (id: string) => !!boxes[id];
  const toggle = (id: string) => setBoxes((b) => ({ ...b, [id]: !b[id] }));
  const openBox = useCallback((id: string) => setBoxes((b) => (b[id] ? b : { ...b, [id]: true })), []);
  const [peek, setPeek] = useState(false); // show the map as it is, without the selected option
  const [acting, setActing] = useState(false);
  const [sensors, setSensors] = useState<Sensors | null | "error">(null);
  const [register, setRegister] = useState<{ key: string; data: RiskRegister | "error" } | null>(null);
  const [record, setRecord] = useState<{ entries: RecordEntry[]; basis: string } | null>(null);
  const refreshRecord = useCallback(() => { la.record().then(setRecord).catch(() => null); }, []);

  useEffect(() => { la.imagery().then(setImagery).catch(() => setImagery(null)); }, []); // is there an aerial picture to offer?
  useEffect(() => { la.buildings().then(setRecords).catch(() => setRecords(null)); }, []);

  // ---------------------------------------------------------------- clock
  useEffect(() => {
    if (!ev) return;
    setPlaying(false); setRevealed(false); setSeries(null); setHour(null); setSelPlan(null); setSelLine(null); setPop(null); setPeek(false);
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
    if (!layers.imagery) q.set("imagery", "0");
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
  // the what-if grid before your switches, to show what each switch does to the lines that stay on
  const [studyBase] = useSim(study && t && studyActions.length ? `${eid}|${t}|base|${JSON.stringify(approvedActions)}|${JSON.stringify(cond)}` : null,
    () => la.simulate(eid, t!, approvedActions, cond), 150);
  const job = study ? stJob : opJob;
  const jobT = study ? stT : opT;
  const plans: Plan[] = useMemo(() => distinctPlans(job?.output?.plans ?? []), [job?.output]);
  const selPlanObj = plans.find((p) => p.id === selPlan) ?? null;
  const done = job?.status === "done" && !!job.output && !!jobT;

  // ---------------------------------------------------------------- options: every choice through the same physics call
  const alarm = hour?.line_alarm ?? s.calibration.fire.line_alarm;
  const planBase = study ? studyAll : NO_ACTIONS; // what the plans were made on top of
  const simKey = (actions: Action[]) => `${eid}|${jobT}|${study ? JSON.stringify(cond) : ""}|${JSON.stringify(actions)}`;
  const simSpec = (actions: Action[]) => ({ key: simKey(actions), run: () => la.simulate(eid, jobT!, actions, study ? cond : undefined) });
  const sims = useSims(done ? [simSpec(planBase), ...plans.map((p) => simSpec([...planBase, ...p.actions]))] : []);
  const nothingSim = done ? sims[simKey(planBase)] ?? null : null;

  // ---------------------------------------------------------------- map colors
  const cur = study && studySim && sameHour(studySim.t_utc, t) ? studySim.lines : null;
  // the selected option's physics, unless the operator asked to see the map without it
  const previewSim = peek || !done || !selPlan ? null : selPlanObj ? sims[simKey([...planBase, ...selPlanObj.actions])] ?? null : null;
  // map colors follow whatever physics is on screen: option preview > Study state > staged changes > recorded hour
  const mapSim = previewSim && sameHour(previewSim.t_utc, t) ? previewSim
    : study ? (studySim && sameHour(studySim.t_utc, t) ? studySim : null)
    : staged.length && stagedSim && sameHour(stagedSim.t_utc, t) ? stagedSim : null;
  const risk = mapSim?.lines.risk ?? hour?.lines.risk ?? [];
  const loading = mapSim?.lines.loading ?? hour?.lines.loading ?? [];
  const energized: boolean[] | null = useMemo(() => {
    if (previewSim) return previewSim.lines.energized;
    if (cur) return cur.energized;
    if (!study && staged.length && stagedSim) return stagedSim.lines.energized;
    if (approved) return s.branches.map((b) => approved.lines[b.id]?.energized ?? true);
    return null;
  }, [previewSim, cur, study, staged.length, stagedSim, approved, s]);
  const colors = useMemo(() => lineColors(s, hour, energized, color, loading, risk, alarm), [s, hour, energized, color, loading, risk, alarm]);
  const dimBuses = (previewSim ? previewSim.buses_out : cur ? studySim!.buses_out : !study && staged.length && stagedSim ? stagedSim.buses_out : []) ?? [];

  // ---------------------------------------------------------------- situation
  const sit = study ? studySim?.situation : opSim && sameHour(opSim.t_utc, t) ? opSim.situation : null;
  const tops = (study ? studySim?.top_lines : opSim && sameHour(opSim.t_utc, t) ? opSim.top_lines : null) ?? hour?.over_alarm ?? [];
  const nHigh = sit ? sit.n_high_risk : hour?.n_over_alarm ?? 0; // the same physics call as the options table
  const isHigh = useCallback((id: string) => { const k = s.branches.findIndex((b) => b.id === id); return k >= 0 && (hour?.lines.risk[k] ?? 0) >= alarm; }, [s, hour, alarm]);
  const labelIds = useMemo(() => labelledLines(color, loading, tops, s), [color, loading, tops, s]);

  // camera: frame the risk area when the clock jumps to an event
  useEffect(() => {
    if (!hour || !pendingFocus.current || hour.event !== eid || hour.i !== i || series?.event !== eid) return;
    pendingFocus.current = false;
    const ids = kind === "heat" ? labelIds : hour.over_alarm.map((l) => l.id);
    setFocus({ ...riskFrame(s, ids), key: Date.now() });
  }, [hour, kind, labelIds, s, eid, i, series]);

  // ---------------------------------------------------------------- sensors, risk register, decision record
  useEffect(() => { // weather stations against the model's weather, for the hour on screen
    setSensors(null);
    if (!t || playing) return;
    let live = true;
    const h = window.setTimeout(() => { la.sensors(eid, t).then((x) => { if (live) setSensors(x); }).catch(() => { if (live) setSensors("error"); }); }, 300);
    return () => { live = false; window.clearTimeout(h); };
  }, [eid, t, playing]);
  const registerActions = study ? studyAll : approvedActions;
  const registerKey = t ? `${eid}|${t}|${JSON.stringify(registerActions)}|${study ? JSON.stringify(cond) : ""}` : null;
  const wantRegister = !!boxes.risk || !!selLine || showReport;
  useEffect(() => { // worked out when first needed, then kept for this hour and these changes
    if (!wantRegister || !registerKey || !t || register?.key === registerKey) return;
    let live = true;
    la.risk(eid, t, registerActions, study ? cond : undefined)
      .then((data) => { if (live) setRegister({ key: registerKey, data }); }).catch(() => { if (live) setRegister({ key: registerKey, data: "error" }); });
    return () => { live = false; };
  }, [wantRegister, registerKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const reg = register && register.key === registerKey ? register.data : null;
  // what switching the selected line off would cut: asked for any line, not only the risky ones the register lists
  const cutKey = selLine && t ? `${eid}|${t}|${selLine}|${JSON.stringify(registerActions)}` : null;
  useEffect(() => {
    if (!cutKey || cutOf?.key === cutKey) return;
    let live = true;
    la.cut(eid, t!, selLine!, registerActions).then((data) => { if (live) setCutOf({ key: cutKey, data }); }).catch(() => null);
    return () => { live = false; };
  }, [cutKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { refreshRecord(); }, [refreshRecord, opJob?.status]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key !== "Escape") return; setSelLine(null); setPop(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selLine]);

  const runCopilot = useCallback(async () => {
    if (!t) return;
    setErr(null); setPlaying(false); setSelPlan(null); setPeek(false); openBox("plan"); openBox("team");
    const risky = hour?.over_alarm.map((l) => l.id) ?? [];
    if (risky.length) setFocus({ ...riskFrame(s, risky, 50), key: Date.now() }); // move in on the trouble while the agents work
    try {
      if (study) { const r = await la.run(eid, t, studyAll.length ? studyAll : [], cond); setStT(t); startSt(r.job_id); }
      else { const r = await la.run(eid, t); setView(r.state); setOpT(t); setOutcome(null); startOp(r.job_id); }
    } catch (e) { setErr(String(e)); }
  }, [t, study, eid, studyAll, cond, startSt, startOp, hour, s, openBox]);

  // The agents go first. In Operate each hour is analysed as soon as the clock rests on it; nobody has to ask.
  // After an approval the result stays on screen until the operator starts over.
  const serverApproved = hasApproval(hour?.copilot);
  useEffect(() => {
    if (study || playing || !t || !hour || hour.t_utc !== t || approvedPlan || serverApproved) return;
    if (opT && sameHour(opT, t)) return; // this hour is analysed, or being analysed
    if (opT) { clearOp(); setOpT(null); setSelPlan(null); return; } // the clock moved: drop the other hour's plan first
    const h = window.setTimeout(() => { runCopilot(); }, 700);
    return () => window.clearTimeout(h);
  }, [study, playing, t, hour, opT, approvedPlan, serverApproved, runCopilot]); // eslint-disable-line react-hooks/exhaustive-deps
  // When the team is done, the screen opens on its pick and the map moves to the lines that plan touches.
  const jobOut = job?.status === "done" ? job.output : null;
  useEffect(() => {
    if (!jobOut || (!study && approvedPlan)) return; // after an approval the plan is not offered again
    const ps = distinctPlans(jobOut.plans);
    const pick = ps.find((p) => p.result.is_safe) ?? ps[0];
    setSelPlan(pick ? pick.id : null); setPeek(false);
    const ids = pick ? [...offLines(pick.actions)] : [];
    if (ids.length) setFocus({ ...riskFrame(s, ids, 40), key: Date.now() });
  }, [jobOut]); // eslint-disable-line react-hooks/exhaustive-deps
  // What a switch does to the lines that stay on, for whichever change is on the map: the selected option or your own.
  const handSim = study ? (studyActions.length ? studySim : null) : staged.length ? stagedSim : null; // your own switches
  const changeSim = previewSim && sameHour(previewSim.t_utc, t) ? previewSim : handSim && sameHour(handSim.t_utc, t) ? handSim : null;
  const baseSim = changeSim && changeSim === previewSim ? nothingSim : study ? studyBase : opSim;
  // a line you put a limit on reads a higher share of its (now lower) rating without carrying any more power: leave it out
  const limited = useMemo(() => new Set([...(study ? studyActions : staged), ...(selPlanObj?.actions ?? [])].filter((a) => a.type === "limit_line").map((a) => a.target)), [study, studyActions, staged, selPlanObj]);
  const impact = useMemo(() => lineImpact(s, baseSim, changeSim, limited), [s, baseSim, changeSim, limited]);
  // move the map out to the lines that pick up the load, so the effect of a switch can be seen as well as read
  const showImpact = impact?.shifted.length ? () => setFocus({ ...riskFrame(s, impact.shifted.slice(0, 6).map((x) => x.id), 40), key: Date.now() }) : undefined;
  const impactOnMap = useMemo(() => (impact?.shifted ?? []).slice(0, 6).map((x) => ({ bi: x.bi, text: `+${Math.round(x.after - x.before)}%` })), [impact]);

  if (!ev) return <div className="loading">Loading…</div>;
  const worst = series ? worstHour(series, kind, i) : null;
  const lineZone = (id: string) => s.branches.find((b) => b.id === id)?.zone;
  const heat = isHeat(kind);
  const staleHours = !past && ev.fetched_utc ? (Date.now() - new Date(ev.fetched_utc).getTime()) / 3.6e6 : 0;

  const numbers = hour && series
    ? situationNumbers({ hour, series, heat, study, past, revealed, sit, nHigh, worst, studySim, onReveal: () => setRevealed(true),
      criticalDownwind: (study ? studySim : opSim && sameHour(opSim.t_utc, t) ? opSim : null)?.fire_exposure.critical ?? null })
    : [];
  const head = hour && worst ? headline(hour, kind, nHigh, tops[0]?.zone ?? null, {
    future: !past && !!series?.now_utc && new Date(hour.t_utc) > new Date(series.now_utc), worst: worst.text,
    regionAct: !study && !heat && hour.region_index >= hour.region_alarm,
    ...(study && sit ? { maxGust: sit.max_gust, minRh: sit.min_rh, maxTemp: sit.max_temp, nOver: sit.n_overloaded, loadMw: sit.load_mw } : {}),
  }) : "Loading the Los Angeles grid…";

  // ---------------------------------------------------------------- plans
  const out = job?.output ?? null;
  const chips = agentChips(nHigh, out, hour);
  const status = (id: string) => (!study ? view?.plan_status[id] : undefined);
  const userPlan: Plan | null = !study && staged.length && stagedSim ? userPlanFrom(staged, stagedSim) : null;
  const approvePlan = async (p: Plan, role: Role) => {
    setActing(true);
    try {
      const r = await la.approve(p.id, role);
      setView(r.state); setBulletinText(r.bulletin.text); setRevealed(true);
      setOutcome(`${planSentence(p, s, lineZone)}${past ? " What actually happened is now on the timeline." : ""}`);
      setSelPlan(null); setPeek(false);
      setApprovedPlan({ off: [...offLines(p.actions)], t: jobT!, busesOut: null });
      // which substations the approved plan leaves without power (for the "got it right" check)
      la.simulate(eid, jobT!, p.actions).then((r) => setApprovedPlan((a) => a && { ...a, busesOut: r.buses_out })).catch(() => null);
    } catch (e) { setErr(String(e)); } finally { setActing(false); refreshRecord(); }
  };
  const rejectPlan = async (p: Plan, reasons: string[], note: string) => { // the reason goes to the coordinator for the next run
    setActing(true);
    try {
      const r = await la.reject(p.id, reasons, note);
      setView((v) => v && { ...v, plan_status: r.plan_status });
    } catch (e) { setErr(String(e)); } finally { setActing(false); refreshRecord(); }
  };
  const resetSession = async () => {
    try {
      const r = await la.reset();
      setRecord((x) => ({ entries: r.entries, basis: x?.basis ?? "" }));
      setView(null); setOutcome(null); setBulletinText(null); setApprovedPlan(null); clearOp(); setOpT(null); setStaged([]); setSelPlan(null); setRevealed(false);
      if (series) la.hour(eid, i).then(setHour).catch(() => null); // the hour no longer carries an approval
    } catch (e) { setErr(String(e)); }
  };

  // every choice at the plans' hour, with numbers from the same physics call
  const options: Option[] = !done ? [] : [
    { key: "NONE", kind: "nothing", label: "Do nothing", sim: nothingSim },
    ...plans.map((p, k): Option => ({
      key: p.id, kind: "plan", label: `Plan ${p.id.replace(/^P/, "")}`, tag: p.from_feedback ? "From your feedback" : k === 0 && p.result.is_safe ? "Agents' pick" : undefined,
      sim: sims[simKey([...planBase, ...p.actions])] ?? null, plan: p, status: status(p.id),
    })),
    ...(userPlan && sameHour(jobT, t) ? [{ key: "YOU", kind: "yours", label: "Your changes", sim: stagedSim, plan: userPlan } as Option] : []),
  ];
  const selOption = options.find((o) => o.key === selPlan) ?? null;
  const optionSentence = (o: Option) => (o.kind === "nothing" ? "Leave every line on." : planSentence(o.plan!, s, lineZone));
  const plansElsewhere = !!jobT && !!t && !sameHour(jobT, t) && done;

  // ---------------------------------------------------------------- "Did Rewire get it right?" (V5 + replay file; never hard-coded)
  const checks = fireChecks(past, series, approvedPlan);

  // ---------------------------------------------------------------- line / bus controls
  const offNow = (id: string, bi: number) => (energized ? !energized[bi] : false) || studyActions.concat(staged).some((a) => a.type === "open_line" && a.target === id);
  const stage = (a: Action) => { if (study) setStudyActions((l) => mergeAction(l, a)); else setStaged((l) => mergeAction(l, a)); setPop(null); };
  const verb = study ? "Applies to the study grid right away." : "Adds to Your changes. Nothing is switched until you approve a plan.";
  const hovered = hover && hour ? s.branches[hover.bi] : null;
  const hoverText = hovered && hover && hour ? lineHoverText(hovered, hover.bi, hour, { energized, color, loading, risk, alarm, cur }) : "";
  const selBi = selLine ? s.branches.findIndex((b) => b.id === selLine) : -1;
  const selText = selBi >= 0 && hour
    ? `${selLine}${s.branches[selBi].name ? ` · ${s.branches[selBi].name}` : ""} · ${Math.round(s.branches[selBi].kv)} kV · ${lineHoverText(s.branches[selBi], selBi, hour, { energized, color, loading, risk, alarm, cur })}`
    : null;
  const findLine = (id: string) => { // select it and bring its midpoint into view
    const g = s.branches.find((b) => b.id === id)?.geometry;
    setSelLine(id); setPop(null); // from a list or the search box: the line opens in the right column
    if (g?.length) { const [lat, lon] = g[Math.floor(g.length / 2)]; setFocus({ lat, lon, spanKm: 40, key: Date.now() }); }
  };
  const simForBus = study ? studySim : opSim;
  const popBus = pop?.kind === "bus" ? s.buses.find((b) => b.id === pop.id) : null;
  const trayActions = study ? studyActions : staged;

  // ---------------------------------------------------------------- the selected line: where it sits in the plans and findings
  const selBranch = selBi >= 0 ? s.branches[selBi] : null;
  const planNotes = !selLine ? [] : plans.flatMap((p) => {
    const name = `Plan ${p.id.replace(/^P/, "")}`;
    const watch = sims[simKey([...planBase, ...p.actions])]?.watch ?? p.result.watch ?? []; // the physics run lists every patrolled line
    if (offLines(p.actions).has(selLine)) return [`${name} turns this line off.`];
    if (watch.some((w) => w.startsWith(`Patrol ${selLine}:`))) return [`${name} keeps it on and sends a patrol: it is an import line that feeds the basin.`];
    return [];
  });
  const lineFindings: LineFinding[] = !selLine || !out?.agent_findings ? [] : Object.entries(out.agent_findings)
    .flatMap(([agent, list]) => list.filter((f) => f.target === selLine).map((f) => ({ agent: AGENT_WORD[agent] ?? agent, issue: f.issue, evidence: f.evidence, severity: f.severity })));

  // ---------------------------------------------------------------- situation report: every feed behind the screen
  const sn = sensors && sensors !== "error" ? sensors : null;
  const vexcel = imagery?.available && imagery.source === "Vexcel";
  const inputs: InputRow[] = [
    { name: "Weather", source: past ? "Open-Meteo (recorded)" : "Open-Meteo forecast", kind: past ? "Recorded" : "Live" },
    { name: "Weather stations", source: past ? "8 airport stations (ASOS)" : "8 airport stations (NWS)", kind: !sn ? "Missing" : past ? "Recorded" : "Live" },
    { name: "Fire warnings", source: "National Weather Service", kind: "Recorded" },
    { name: "Aerial imagery and trees", source: vexcel ? "Vexcel" : imagery?.source ?? "not built", kind: vexcel ? "Recorded" : imagery?.available ? "Stand-in" : "Missing" },
    { name: "Buildings", source: records?.costar ? `CoStar (${records.costar.toLocaleString("en-US")} properties)` : "OpenStreetMap hospitals, water plants", kind: records?.costar ? "Recorded" : "Stand-in" },
    { name: "Power lines", source: "OpenStreetMap, 66 kV and up", kind: "Modelled" },
    { name: "Demand", source: past ? "EIA hourly demand" : "Demand model", kind: past ? "Recorded" : "Modelled" },
    { name: "People", source: "2020 Census", kind: "Recorded" },
    { name: "Real outages", source: "EAGLE-I county records", kind: "Recorded" },
  ];

  // a line clicked on the map opens as a pop-up at the click; a line picked from a list opens as a box in the right dock
  const popLine = pop?.kind === "line" && selBranch?.id === pop.id ? pop : null;
  const lineView = selBranch && hour && (
    <LineSheet key={selBranch.id} line={selBranch} bi={selBi} hour={hour} lines={mapSim?.lines ?? cur} alarm={alarm} isOff={offNow(selBranch.id, selBi)}
      capPct={trayActions.find((a) => a.type === "limit_line" && a.target === selBranch.id)?.pct} verb={verb}
      register={reg && reg !== "error" ? reg.rows.find((r) => r.id === selBranch.id) : undefined} top={tops.find((l) => l.id === selBranch.id)}
      cut={cutOf && cutOf.key === cutKey ? cutOf.data : undefined}
      planNotes={planNotes} findings={lineFindings} modelText={out?.mode === "llm"} at={popLine ? { x: popLine.x, y: popLine.y } : undefined}
      onAction={(a) => stage({ ...a, target: selBranch.id })} onShow={() => findLine(selBranch.id)} onClose={() => { setSelLine(null); setPop(null); }} />
  );
  // the priority ladder shows what the option on screen cuts off, rung by rung
  const ladderOf = selOption && selOption.kind !== "nothing" ? selOption : options.find((o) => o.kind === "plan") ?? null;
  const ladder = ladderOf?.sim?.buildings_out?.ladder ? { label: ladderOf.label, rungs: ladderOf.sim.buildings_out.ladder } : null;

  // ---------------------------------------------------------------- what each box says while it is closed
  const liveLines = study ? studySim?.lines ?? null : opSim && sameHour(opSim.t_utc, t) ? opSim.lines : null;
  const health = hour ? lineHealth(s, hour, liveLines, heat, alarm) : null;
  const nOver = study ? sit?.n_overloaded ?? 0 : hour?.n_overloaded ?? 0;
  const nBad = heat ? nOver : nHigh;
  const metricLabel = heat ? (nOver === 1 ? "line carrying more than its rating" : "lines carrying more than their rating")
    : nHigh === 1 ? "power line at high fire risk" : "power lines at high fire risk";
  const cause = head.split(/(?<=\.)\s/)[0];
  const running = job?.status === "running";
  const analysed = !!jobT && sameHour(jobT, t);
  const goTo = (iso: string | null) => { if (series && iso) setI(series.t_utc.findIndex((x) => sameHour(x, iso))); };
  const onNav = (cmd: NavCmd) => {
    if (cmd === "home") { const b = s.bbox; setFocus({ lat: (b.lat_min + b.lat_max) / 2, lon: (b.lon_min + b.lon_max) / 2, spanKm: 95, key: Date.now() }); }
    else setNav({ cmd, key: Date.now() });
  };
  const nPlans = options.filter((o) => o.kind === "plan").length;
  const planSummary = running ? <span className="sum-busy">agents are working</span>
    : !done ? (approvedPlan || serverApproved ? "approved" : study ? "ask the agents" : "waiting for the agents")
    : outcome && !study ? <span className="sum-ok">approved</span>
    : !nPlans ? <span className="sum-ok">nothing to do</span>
    : selOption?.sim ? <>{selOption.label} · <span className={selOption.sim.is_safe ? "sum-ok" : "sum-act"}>{selOption.sim.is_safe ? "safe" : "not safe"}</span></> : `${nPlans} ready`;
  const teamSummary = !job ? "standing by" : running ? <span className="sum-busy">analysing</span> : job.status === "error" ? <span className="sum-act">stopped</span> : <span className="sum-ok">done</span>;
  const sensorSummary = sensors === "error" ? <span className="sum-act">not available</span> : !sn ? "loading"
    : sn.level === "warn" ? <span className="sum-warn">▲ {sn.model_low.length + sn.drier.length} need a look</span> : `${sn.reporting} of ${sn.n} stations match`;
  const affected = selOption && selOption.kind !== "nothing" ? selOption.sim : null;
  // what the cut costs the customers it reaches: published survey rates times the customers cut off (backend/la/costs.py)
  const bill = affected?.cost?.customers ? affected.cost : null;
  const costLine = bill ? `Costs the customers cut off about ${usd(bill.usd["8"])} over 8 hours, ${usd(bill.usd["24"])} over a day.` : null;
  const costNote = bill ? `Rough. ${bill.source}; assumes 1 customer in ${Math.round(1 / bill.business_share)} is a business.` : null;

  return (
    <div className={`screen ${study ? "study" : ""}`}>
      <div className="map-full">
        <LAScene s={s} hour={hour} colors={colors} layers={layers} imagery={imagery?.available ? imageryUrl(imagery.built_utc ?? "") : null}
          surround={imagery?.surround ? { url: surroundUrl(imagery.surround.built_utc), bbox: imagery.surround.bbox } : null} nav={nav} impact={impactOnMap}
          labelIds={labelIds} selected={selLine} dimBuses={dimBuses}
          showFires={past && revealed} focus={focus} overlay={overlay}
          onHoverLine={(bi, x, y) => setHover(bi == null ? null : { bi, x: x!, y: y! })}
          onClickLine={(bi, x, y) => { setSelLine(s.branches[bi].id); setPop({ kind: "line", id: s.branches[bi].id, x, y }); }}
          onClickBus={(id, x, y) => { setSelLine(null); setPop({ kind: "bus", id, x, y }); }}
          onMiss={() => { setPop(null); setSelLine(null); }} />
        <div className="labels" ref={overlay} />
      </div>
      {study && <div className="study-frame" />}
      {slot && series && createPortal(
        <Timeline series={series} kind={kind} i={i} playing={playing} revealed={revealed} canReveal={past}
          onSeek={setI} onPlay={() => setPlaying((p) => !p)} onReveal={() => { setRevealed(true); openBox("happened"); }} />, slot)}

      {/* left: the agents and how they got there */}
      <aside className="dock left">
        <Section title="Agent team" summary={teamSummary} open={isOpen("team")} onToggle={() => toggle("team")}>
          {job ? (
            <TeamPanel job={job} chips={chips} shownPlans={plans.map((p) => p.id)} selectedPlan={selPlan}
              onPlan={(id) => { setSelPlan(id); setPeek(false); openBox("plan"); }} onLine={findLine} />
          ) : (
            <>
              <p className="muted">{study ? "In What-if the agents wait until you ask them." : playing ? "The agents wait while the clock is running. Pause to have them analyse the hour." : approvedPlan || serverApproved ? "A plan is approved. Start over from the log to analyse again." : "Five agents analyse each hour as soon as the clock rests on it: fire, wind, heat, brownout and buildings."}</p>
              {study && <button className="primary" disabled={!hour} onClick={runCopilot}>Ask the agents about this what-if</button>}
            </>
          )}
        </Section>
        {records && (
          <Section title="Who is protected first" summary={ladder ? `cut off by ${ladder.label}` : `${records.n.toLocaleString("en-US")} buildings`} open={isOpen("ladder")} onToggle={() => toggle("ladder")}>
            <PriorityLadder records={records} ladder={ladder} />
          </Section>
        )}
        {job && (
          <Section title="Agent log" summary={`${job.log.length} lines`} open={isOpen("agentlog")} onToggle={() => toggle("agentlog")}>
            <AgentLog job={job} />
          </Section>
        )}
      </aside>

      {/* middle: the map, with its own small controls */}
      <div className="map-tools">
        <LayersMenu color={color} layers={layers} hasStorm={!!hour?.line_damage} imagerySource={imagery?.available ? imagery.source : null}
          onColor={setColor} onLayers={setLayers} />
        <LineSearch s={s} selected={selText} onFind={(id) => { findLine(id); openBox("line"); }} />
        {study && <ConditionsPanel value={cond} onChange={setCond} />}
      </div>
      <NavPad onNav={onNav} />
      {hovered && hover && !pop && (
        <div className="hover-tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <b>{hovered.id}{hovered.name ? ` · ${hovered.name}` : ""}</b>
          <span className="muted">{Math.round(hovered.kv)} kV · {energized && !energized[hover.bi] ? "off" : "on"}</span>
          <span>{hoverText}</span>
          <span className="muted small">Click to open this line</span>
        </div>
      )}
      {popLine && lineView}
      {popBus && pop && (
        <LineControls key={popBus.id} x={pop.x} y={pop.y} kind="bus" title={popBus.name} sub={`Substation · ${Math.round(popBus.kv)} kV`}
          maxShed={simForBus?.bus_load[popBus.id]} verb={study ? "Applies to the what-if grid right away." : "Adds to Your changes."}
          onAction={(a) => stage({ ...a, target: popBus.id })} onClose={() => setPop(null)} />
      )}
      <div className="tray-slot">
        <ChangesTray s={s} actions={trayActions} result={study ? studySim : stagedSim} loading={study ? studyBusy : stagedBusy} study={study}
          impact={changeSim && changeSim === handSim && impact ? impact.text : null} onShowImpact={showImpact}
          onRemove={(k) => (study ? setStudyActions : setStaged)((l) => l.filter((_, j) => j !== k))}
          onClear={() => (study ? setStudyActions([]) : setStaged([]))}
          onCopyToPlan={study ? () => { setStaged(studyActions.filter((a) => GRID_CHANGES.has(a.type))); onMode("operate"); } : undefined} />
      </div>

      {/* right: stacked drop-down boxes. Closed, each shows its one fact; the plan starts open because it is the main thing */}
      <aside className="dock right">
        {!online && <div className="banner">▲ <b>Can't reach the Rewire backend.</b> Numbers may be out of date. Retrying…</div>}
        {staleHours >= STALE_AFTER_H && <div className="banner">▲ <b>This forecast is {Math.round(staleHours)} hours old.</b> Press Refresh at the top.</div>}
        {sn?.level === "warn" && <div className="banner">▲ {sensorsSentence(sn)} <button className="link" onClick={() => openBox("sensors")}>See the stations</button></div>}
        {err && <div className="error" onClick={() => setErr(null)}>{err}</div>}

        <Section title="Grid status" open={isOpen("status")} onToggle={() => toggle("status")} tone={study ? "study" : undefined}
          summary={<><b className={nBad ? "sum-act" : "sum-ok"}>{nBad}</b> {heat ? "over rating" : "at high fire risk"}{health ? <> · <b>{health.normal}</b> of {health.total} fine</> : null}</>}>
          <StatusBox health={health} metric={String(nBad)} label={metricLabel} cause={cause} tone={nBad ? "act" : "ok"} onReport={() => setShowReport(true)} />
        </Section>

        <Section title="The plan" main summary={planSummary} open={isOpen("plan")} onToggle={() => toggle("plan")}>
          {!study && outcome && <div className="outcome"><b>Plan approved.</b> {outcome}</div>}
          {!study && bulletinText && (
            <details className="drawer">
              <summary>Bulletin for responders <button className="link" onClick={(e) => { e.preventDefault(); navigator.clipboard.writeText(bulletinText); }}>Copy</button></summary>
              <pre className="bulletin">{bulletinText}</pre>
            </details>
          )}
          {!study && !job && (approvedPlan || serverApproved) && !outcome
            ? <p className="muted">A plan was approved for this hour. <button className="link" onClick={resetSession}>Start over</button> to analyse it again.</p>
            : (
              <PlanPanel running={running} done={done} options={options} selected={selOption} base={nothingSim} s={s} heat={heat} peek={peek}
                study={study} stale={plansElsewhere} canAct={!study && analysed} busy={acting} nRecord={record?.entries.length ?? 0}
                staleNote={plansElsewhere ? `This plan is for ${timeLabel(jobT!)}.` : out?.no_safe_option ? "No safe plan was found this hour. The closest one is shown and can't be approved." : null}
                onStale={plansElsewhere ? () => goTo(jobT) : undefined}
                impact={changeSim === previewSim && impact ? impact.text : null} onShowImpact={showImpact} cost={costLine} costNote={costNote} describe={optionSentence}
                onSelect={(k) => { setSelPlan(k); setPeek(false); }} onPeek={setPeek} onAffected={() => openBox("affected")}
                onApprove={(role) => selOption?.plan && approvePlan(selOption.plan, role)} onReject={(reasons, note) => selOption?.plan && rejectPlan(selOption.plan, reasons, note)}
                onRecord={() => { refreshRecord(); openBox("log"); }} onRerun={runCopilot} />
            )}
        </Section>

        {selBranch && !popLine && (
          <Section title={`Line ${selBranch.id}`} summary={selBranch.name ?? `${Math.round(selBranch.kv)} kV`} open={boxes.line !== false} onToggle={() => setBoxes((b) => ({ ...b, line: b.line === false }))}
            onClose={() => setSelLine(null)}>
            {lineView}
          </Section>
        )}
        {affected && (
          <Section title="Who is affected" open={isOpen("affected")} onToggle={() => toggle("affected")}
            summary={`${affected.buildings_out?.critical ?? 0} critical sites${affected.buildings_out?.homes ? ` · ${affected.buildings_out.homes.toLocaleString("en-US")} homes` : ""}`}>
            <AffectedSheet option={selOption!} onLine={setSelLine} />
          </Section>
        )}
        {revealed && checks.length > 0 && (
          <Section title="What really happened" summary={past ? "from the record" : ""} open={isOpen("happened")} onToggle={() => toggle("happened")}>
            <ul className="happened">{checks.map((c) => <li key={c.text} className={c.lead ? "lead" : ""}>{c.text}</li>)}</ul>
          </Section>
        )}
        <Section title="Risk by line" summary={health ? `${health.high} high · ${health.elevated} elevated` : ""} open={isOpen("risk")} onToggle={() => toggle("risk")}>
          <RiskSheet reg={reg} onLine={setSelLine} />
        </Section>
        <Section title="Sensors" summary={sensorSummary} open={isOpen("sensors")} onToggle={() => toggle("sensors")}>
          <InputsSheet inputs={inputs} sensors={sensors} />
        </Section>
        <Section title="Log" summary={`${record?.entries.length ?? 0} entries`} open={isOpen("log")} onToggle={() => { refreshRecord(); toggle("log"); }}>
          <RecordSheet entries={record?.entries ?? null} onReset={resetSession} />
        </Section>
      </aside>
      {showReport && hour && (
        <ReportPage title={eventLabel(ev)} when={timeLabel(hour.t_utc, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
          basis={past ? "Recorded weather" : "Live forecast"} study={study} headline={head} numbers={numbers} hour={hour} tops={tops}
          job={job} chips={chips} options={options} describe={optionSentence} outcome={study ? null : outcome} bulletin={study ? null : bulletinText}
          inputs={inputs} sensors={sn} register={reg && reg !== "error" ? reg : null} record={record?.entries ?? []} onClose={() => setShowReport(false)} />
      )}
    </div>
  );
}
