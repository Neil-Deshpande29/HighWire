/** Pure derivations for the LA screen: everything LAApp shows that is computed from data rather than held as state.
 *  No React in this file, so each rule can be read (and tested) on its own. */
import type { Action, CopilotOutput, Plan } from "../api";
import { fmtInt, fmtPeople, mph, offLines, offTargets, timeLabel } from "../ui/format";
import type { ColorLayer } from "../ui/LayersMenu";
import type { AgentChip } from "../ui/PlanList";
import type { BigNumber } from "../ui/SituationCard";
import type { Branch, Hour, LAStatic, Series, SimResult } from "./api";
import { C, damageColor, loadColor, riskColor } from "./LAScene";

/** What the operator approved in this session: lines switched off, the plan's hour, substations left without power. */
export type ApprovedPlan = { off: string[]; t: string; busesOut: string[] | null };
export type Check = { text: string; lead?: boolean };
type Situation = SimResult["situation"];

export const sameHour = (a?: string | null, b?: string | null) => !!a && !!b && Math.abs(new Date(a).getTime() - new Date(b).getTime()) < 1800e3;
export const GRID_CHANGES = new Set(["open_line", "close_line", "limit_line", "shed_load", "deenergize_lines", "deenergize_bus", "dispatch_gen"]);

/** The planner can return two plans that switch the same lines: show one. */
export function distinctPlans(plans: Plan[]): Plan[] {
  const seen = new Set<string>();
  return plans.filter((p) => {
    const k = JSON.stringify([[...offLines(p.actions)].sort(), p.actions.filter((a) => !offTargets(a).length)]);
    return seen.has(k) ? false : (seen.add(k), true);
  });
}

/** One colour per line, in branch order, for the layer the operator chose. */
export function lineColors(s: LAStatic, hour: Hour | null, energized: boolean[] | null, color: ColorLayer, loading: number[], risk: number[], alarm: number) {
  return s.branches.map((b, bi) => {
    if (!hour) return C.none;
    if (energized && !energized[bi]) return C.off;
    if (color === "loading") return loadColor(loading[bi]);
    if (b.cable) return C.cable;
    if (color === "storm") return hour.line_damage ? damageColor(hour.line_damage[bi]) : C.none;
    return riskColor(risk[bi], alarm);
  });
}

/** Up to eight lines to label: the most loaded on the loading layer, otherwise the riskiest. */
export function labelledLines(color: ColorLayer, loading: number[], tops: { id: string }[], s: LAStatic): string[] {
  if (color === "loading") return loading.map((v, k) => [v, k] as const).filter(([v]) => v >= 80).sort((a, b) => b[0] - a[0]).slice(0, 8).map(([, k]) => s.branches[k].id);
  return tops.slice(0, 8).map((l) => l.id);
}

/** Where to point the camera for a set of lines: their median midpoint, wide enough for 80% of them. */
export function riskFrame(s: LAStatic, ids: string[]): { lat: number; lon: number; spanKm: number } {
  const pts = ids.map((id) => s.branches.find((b) => b.id === id)?.geometry).filter(Boolean).map((g) => g![Math.floor(g!.length / 2)]);
  const b = s.bbox;
  if (!pts.length) return { lat: (b.lat_min + b.lat_max) / 2, lon: (b.lon_min + b.lon_max) / 2, spanKm: 150 };
  const med = (v: number[]) => [...v].sort((x, y) => x - y)[Math.floor(v.length / 2)];
  const lat = med(pts.map((p) => p[0])), lon = med(pts.map((p) => p[1]));
  const dist = pts.map((p) => Math.hypot((p[0] - lat) * 110.5, (p[1] - lon) * 92)).sort((x, y) => x - y);
  const span = 2.4 * dist[Math.floor(0.8 * (dist.length - 1))];
  return { lat, lon, spanKm: Math.min(120, Math.max(65, span)) };
}

/** The four big numbers on the Situation card. */
export function situationNumbers(a: {
  hour: Hour; series: Series; heat: boolean; study: boolean; past: boolean; revealed: boolean; sit: Situation | null | undefined;
  nHigh: number; worst: { text: string; sub: string } | null; studySim: SimResult | null; onReveal: () => void;
}): BigNumber[] {
  const { hour, series, heat, study, past, revealed, sit, nHigh, worst, studySim, onReveal } = a;
  const numbers: BigNumber[] = [];
  if (heat) {
    numbers.push({ value: String(study ? sit?.n_overloaded ?? "…" : hour.n_overloaded), label: "lines over their rating", tone: (study ? sit?.n_overloaded : hour.n_overloaded) ? "act" : "ok" });
    numbers.push({ value: `${Math.round(study ? sit?.max_loading ?? 0 : hour.max_loading)}%`, label: "busiest line's load", sub: "share of its rating" });
  } else {
    numbers.push({ value: String(nHigh), label: "power lines at high fire risk", tone: nHigh ? "act" : "ok" });
    numbers.push({ value: sit ? fmtPeople(sit.people_downwind) : "…", label: "people downwind of them" });
    numbers.push({ value: sit ? String(sit.hospitals_downwind + sit.water_plants_downwind) : "…", label: "hospitals and water plants downwind" });
  }
  if (study) numbers.push({ value: studySim ? fmtInt(studySim.customers_out) : "…", label: "customers losing power", sub: "in this study" });
  else if (past && revealed) {
    const o = hour.outages["Los Angeles"];
    numbers.push(o == null ? { value: "n/a", label: "customers really out", sub: "no record this hour", tone: "muted" } : { value: fmtInt(o), label: "customers really out", sub: "LA County record" });
  } else numbers.push({ value: worst?.text ?? "", label: "worst hour", sub: worst?.sub });
  if (!past && !study && numbers.length < 4) {
    const k0 = series.now_utc ? Math.max(0, series.t_utc.findIndex((x) => new Date(x) >= new Date(series.now_utc!))) : 0;
    numbers.push({ value: `${(Math.max(...series.load_mw.slice(k0)) / 1000).toFixed(1)} GW`, label: "peak demand ahead" });
  }
  void onReveal;
  return numbers;
}

/** One summary per agent for the chips above the plans. */
export function agentChips(nHigh: number, out: CopilotOutput | null, hour: Hour | null): AgentChip[] {
  return [
    { id: "fire_agent", name: "Fire agent", summary: `${nHigh} line${nHigh === 1 ? "" : "s"} flagged` },
    { id: "wind_agent", name: "Wind agent", summary: out?.crew_staging?.above_alarm ? "storm damage likely" : "no storm alarm" },
    { id: "heat_agent", name: "Heat agent", summary: hour?.n_overloaded ? `${hour.n_overloaded} lines hot` : "no overheating" },
    { id: "brownout_agent", name: "Brownout agent", summary: out && out.do_nothing.customers_out ? `${fmtInt(out.do_nothing.customers_out)} at risk` : "no shortfalls" },
    { id: "buildings_agent", name: "Buildings agent", summary: "who loses power, by priority" },
  ];
}

/** The operator's staged changes, shaped like a plan so they can sit beside the co-pilot's. */
export function userPlanFrom(staged: Action[], sim: SimResult): Plan {
  return {
    id: "YOU", title: "Your plan", actions: staged, steps: staged.map((a) => ({ action: a, why: "", raised_by: [] })),
    result: { is_safe: sim.is_safe, unserved_mw: sim.unserved_mw, customers_out: sim.customers_out, violations: sim.violations,
              critical_on_backup: sim.critical_on_backup, n1_warnings: [], watch: sim.watch,
              hospitals_on_backup: sim.hospitals_on_backup, buildings_out: sim.buildings_out },
    tradeoffs: "", confidence: 1, assumptions: [], attempts: 1,
  };
}

/** "Did Highwire get it right?": one sentence per fact, from V5 and the replay file; never hard-coded. */
export function fireChecks(past: boolean, series: Series | null, approvedPlan: ApprovedPlan | null): Check[] {
  const checks: Check[] = [];
  if (!past || !series) return checks;
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
  return checks;
}

/** The sentence in the hover tip, for the layer on screen. `cur` is the Study physics when it applies. */
export function lineHoverText(branch: Branch, k: number, hour: Hour, a: {
  energized: boolean[] | null; color: ColorLayer; loading: number[]; risk: number[]; alarm: number; cur: SimResult["lines"] | null;
}): string {
  const { energized, color, loading, risk, alarm, cur } = a;
  if (energized && !energized[k]) return "Off: no fire risk from this line.";
  if (color === "loading") return `Carrying ${Math.round(loading[k])}% of its rating.`;
  if (color === "storm" && hour.line_damage) return `${Math.round(hour.line_damage[k] * 100)}% chance wind damages it this hour.`;
  const g = cur?.gust[k] ?? hour.lines.gust[k], rh = cur?.rh[k] ?? hour.lines.rh[k];
  if (branch.cable) return "Underground cable: no wind or fire exposure.";
  const lvl = risk[k] >= alarm ? "High fire risk" : risk[k] >= 0.6 * alarm ? "Elevated fire risk" : "Normal fire risk";
  return `${lvl}: gusts ${mph(g)} mph, humidity ${Math.round(rh)}%.`;
}
