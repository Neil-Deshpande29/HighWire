// Plain-language helpers. Every sentence here is built from fields the backend already returns; no LLM, no new numbers.
import type { Action, Plan } from "../api";
import type { Hour, LAEvent, LAStatic, Series } from "../la/api";

export const TZ = "America/Los_Angeles";
export const mph = (ms: number) => Math.round(ms * 2.237);
export const degF = (c: number) => Math.round(c * 9 / 5 + 32);
export const fmtInt = (n: number) => Math.round(n).toLocaleString("en-US");
export const fmtPeople = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : fmtInt(n));

export const timeLabel = (iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric", hour: "numeric" }) =>
  new Date(iso).toLocaleString("en-US", { timeZone: TZ, ...opts });

/** Plain name for each past event in the "Go to" menu (calm control weeks are Evidence-only). */
const EVENT_LABEL: Record<string, string> = {
  E1: "Woolsey fire · Nov 2018", E2: "Saddleridge fire · Oct 2019", E3: "Eaton & Hurst fires · Jan 2025",
  H1: "Heat wave · Aug 2020", H2: "Heat wave · Sep 2022",
};
export function eventLabel(e: LAEvent): string {
  if (e.kind === "forecast") return "Now · live forecast";
  if (EVENT_LABEL[e.id]) return EVENT_LABEL[e.id];
  const when = new Date(e.start).toLocaleString("en-US", { month: "short", year: "numeric" });
  return `${e.kind === "heat" ? "Heat wave" : e.name.split("(")[0].trim()} · ${when}`;
}
export const inGoTo = (e: LAEvent) => e.kind !== "control" && e.has_replay;

const ZONE_WORD: Record<string, string> = { mountain: "in the mountains and foothills", valley: "in the valleys", coast: "along the coast", basin: "in the LA basin" };

/** Fire-risk curve on wind days, peak line loading on heat days. */
export const isHeat = (kind: string) => kind === "heat";
/** Timeline curve: power lines at high fire risk (a plain count) on wind days, busiest line's % of rating on heat days. */
export const curveOf = (s: Series, kind: string) => (isHeat(kind) ? s.max_loading : s.n_high_risk);
export const thresholdOf = (_s: Series, kind: string) => (isHeat(kind) ? 100 : 1);

/** Hour to open an event at: first hour the curve reaches "act now", else its worst hour. */
export function startIndex(s: Series, kind: string): number {
  if (s.now_utc) return Math.max(0, s.t_utc.findIndex((t) => new Date(t).getTime() >= new Date(s.now_utc!).getTime()));
  const c = curveOf(s, kind), thr = thresholdOf(s, kind);
  const k = c.findIndex((v) => v >= thr);
  return k >= 0 ? k : c.indexOf(Math.max(...c));
}

export function worstHour(s: Series, kind: string, i: number): { k: number; text: string; sub: string } {
  const c = curveOf(s, kind);
  const k = c.indexOf(Math.max(...c));
  const dh = k - i;
  const text = dh === 0 ? "Now" : dh > 0 ? `in ${dh} h` : `${-dh} h ago`;
  return { k, text, sub: timeLabel(s.t_utc[k], { weekday: "short", hour: "numeric" }) };
}

function santaAna(h: Hour): boolean {
  const w = h.weather;
  let best = -1, dir = 0;
  w.gust.forEach((row, j) => row.forEach((g, i) => { if (g > best) { best = g; dir = w.dir[j][i]; } }));
  return dir >= 0 && dir <= 110; // offshore (from the N-NE-E)
}

/** Headline sentence for the Situation card. */
export function headline(h: Hour, kind: string, nHigh: number, topZone: string | null, opts: { future: boolean; worst: string; regionAct?: boolean; maxGust?: number; minRh?: number; maxTemp?: number; nOver?: number; loadMw?: number }): string {
  const gust = opts.maxGust ?? h.weather.max_gust, rh = opts.minRh ?? h.weather.min_rh, temp = opts.maxTemp ?? h.weather.max_temp;
  const when = opts.future ? `${timeLabel(h.t_utc, { weekday: "long", hour: "numeric" })}: ` : "";
  if (isHeat(kind)) {
    const over = opts.nOver ?? h.n_overloaded;
    const gw = ((opts.loadMw ?? h.load_mw) / 1000).toFixed(1);
    return `${when}Heat up to ${degF(temp)} °F inland pushes demand to ${gw} GW. ` +
      (over ? `${over} power line${over === 1 ? " is carrying more than it's" : "s are carrying more than they're"} rated for.` : "Every line is within its rating.");
  }
  const strength = gust >= 25 ? "Extreme" : gust >= 17 ? "Strong" : gust >= 11 ? "Gusty" : "Light";
  const sa = gust >= 11 && santaAna(h) ? " Santa Ana" : "";
  const where = topZone && nHigh ? ` ${ZONE_WORD[topZone] ?? ""}` : "";
  const dry = rh <= 15 ? ` and very dry air (${Math.round(rh)}% humidity)` : rh <= 25 ? ` and dry air (${Math.round(rh)}% humidity)` : "";
  const wind = `${strength}${sa} winds (gusts to ${mph(gust)} mph)${dry}${where}.`;
  const peak = opts.worst !== "Now" && !opts.worst.endsWith("ago") ? `; risk peaks ${opts.worst}` : "";
  const risk = nHigh ? ` ${nHigh} power line${nHigh === 1 ? " is" : "s are"} at high fire risk.`
    : opts.regionAct ? ` Fire danger across the region is at the act-now level, but no single line is at high risk yet${peak}.`
    : ` No power lines are at high fire risk${peak}.`;
  return `${when}${wind}${risk}`;
}

// ---------------------------------------------------------------- actions and plans
export const offTargets = (a: Action): string[] => (a.type === "open_line" ? [a.target] : a.type === "deenergize_lines" ? a.targets ?? [] : []);
export const offLines = (acts: Action[]) => new Set(acts.flatMap(offTargets));

export function actionText(a: Action, s?: LAStatic): string {
  const bus = (id: string) => s?.buses.find((b) => b.id === id)?.name ?? id;
  switch (a.type) {
    case "open_line": return `Turn off line ${a.target}`;
    case "close_line": return `Turn on line ${a.target}`;
    case "deenergize_lines": return `Turn off ${a.targets?.length ?? 0} lines (${a.target})`;
    case "limit_line": return `Limit line ${a.target} to ${Math.round(a.pct ?? 100)}% of its rating`;
    case "shed_load": return `Cut ${Math.round(a.mw ?? 0)} MW of demand at ${bus(a.target)}`;
    case "dispatch_gen": return `Set import ${a.target} to ${Math.round(a.mw ?? 0)} MW`;
    case "deenergize_bus": return `Turn off substation ${bus(a.target)}`;
    default: return `${a.type} ${a.target}`;
  }
}

/** Merge a new change into a list: one entry per (kind, target). */
export function mergeAction(list: Action[], a: Action): Action[] {
  const kindOf = (x: Action) => (x.type === "open_line" || x.type === "close_line" ? "switch" : x.type);
  const rest = list.filter((x) => !(x.target === a.target && kindOf(x) === kindOf(a)));
  if (a.type === "limit_line" && (a.pct ?? 100) >= 100) return rest;
  if (a.type === "shed_load" && (a.mw ?? 0) <= 0) return rest;
  return [...rest, a];
}

const BUILDING_WORDS: Record<string, [string, string]> = {
  hospital: ["hospital", "hospitals"], water: ["water plant", "water plants"], emergency: ["emergency service", "emergency services"],
  medical: ["medical site", "medical sites"], data_center: ["data center", "data centers"], cold_storage: ["cold store", "cold stores"],
  senior_housing: ["senior housing site", "senior housing sites"],
  school: ["school", "schools"], residential: ["apartment building", "apartment buildings"], industrial: ["industrial site", "industrial sites"],
  lodging: ["hotel", "hotels"], office: ["office", "offices"], retail: ["shop", "shops"], other: ["other building", "other buildings"],
};
/** "hospital", "water plant": the word for one building of a kind. */
export const kindWord = (k: string) => (BUILDING_WORDS[k] ?? [k.replace("_", " ")])[0];
/** ", 3,668 homes" when CoStar knows how many apartment homes are in the set; "" otherwise. */
export const homesText = (homes?: number | null) => (homes ? ` (${fmtInt(homes)} homes)` : "");
/** "hospitals", "water plants": the plural word for a building kind. */
export const kindPlural = (k: string) => (BUILDING_WORDS[k] ?? [k, `${k}s`])[1];
/** "1 hospital · 4 water plants" from counts by kind (the backend sends them heaviest first). */
export function buildingsText(byKind: Record<string, number>, sep = " · "): string {
  return Object.entries(byKind).map(([k, n]) => `${fmtInt(n)} ${(BUILDING_WORDS[k] ?? [k, `${k}s`])[n === 1 ? 0 : 1]}`).join(sep);
}

export function hospitalsOf(critical: string[], s: LAStatic): number {
  const hosp = new Set(s.critical_sites.filter((c) => c.type === "hospital").map((c) => c.bus));
  return critical.filter((b) => hosp.has(b)).length;
}

/** One plain sentence: what the plan does. */
export function planSentence(p: Plan, s: LAStatic, lineZone: (id: string) => string | undefined): string {
  const off = [...offLines(p.actions)];
  const parts: string[] = [];
  if (off.length) {
    const zones = off.map(lineZone).filter(Boolean) as string[];
    const top = zones.sort((a, b) => zones.filter((z) => z === b).length - zones.filter((z) => z === a).length)[0];
    parts.push(`Turn off ${off.length} line${off.length === 1 ? "" : "s"}${top ? ` ${ZONE_WORD[top] ?? ""}` : ""}`);
  }
  const shed = p.actions.filter((a) => a.type === "shed_load");
  if (shed.length) parts.push(`cut ${fmtInt(shed.reduce((x, a) => x + (a.mw ?? 0), 0))} MW of demand`);
  const lim = p.actions.filter((a) => a.type === "limit_line");
  if (lim.length) parts.push(`limit ${lim.length} line${lim.length === 1 ? "" : "s"}`);
  const other = p.actions.filter((a) => !["open_line", "deenergize_lines", "shed_load", "limit_line"].includes(a.type));
  if (other.length) parts.push(other.map((a) => actionText(a, s).toLowerCase()).join(", "));
  const what = parts.length ? parts.join(", ").replace(/^./, (c) => c.toUpperCase()) + "." : "Change nothing.";
  const r = p.result;
  const cust = r.customers_out ? `${fmtInt(r.customers_out)} customers lose power` : "No customers lose power";
  const h = r.hospitals_on_backup ?? hospitalsOf(r.critical_on_backup, s); // every hospital behind a cut-off substation, not one per substation
  return `${what} ${cust}; ${h ? `${h} hospital${h === 1 ? "" : "s"} go${h === 1 ? "es" : ""} to backup power` : "all hospitals stay on"}.`;
}

/** Physics violation -> short plain reason. */
export function plainViolation(v: string): string {
  let m = v.match(/^Line (\S+): fire risk/);
  if (m) return `line ${m[1]} stays on at high fire risk`;
  m = v.match(/^Line (\S+): loading (\d+)% of the operator limit/);
  if (m) return `line ${m[1]} would exceed your limit (${m[2]}%)`;
  m = v.match(/^Line (\S+): loading (\d+)%/);
  if (m) return `line ${m[1]} would be overloaded (${m[2]}% of rating)`;
  m = v.match(/^Line (\S+): conductor/);
  if (m) return `line ${m[1]} would overheat`;
  m = v.match(/^Line (\S+): clearance/);
  if (m) return `line ${m[1]} would swing too close to trees`;
  m = v.match(/^Blackout: .* is (\d+)% of regional load/);
  if (m) return `cuts power to ${m[1]}% of the region (limit 10%)`;
  if (v.startsWith("Invalid action")) return v.replace("Invalid action: ", "");
  return v;
}

const AGENT_WORDS: Record<string, string> = { fire_agent: "the Fire agent", wind_agent: "the Wind agent", heat_agent: "the Heat agent",
  brownout_agent: "the Brownout agent", coordinator: "the Coordinator" };
/** Text an agent or the planner wrote, in the screen's own units and names: mph, °C, "the Fire agent". Values are unchanged. */
export function plainAgentText(t: string): string {
  const s = t.replace(/(\d+(?:\.\d+)?) m\/s/g, (_, v) => `${mph(+v)} mph`).replace(/(\d+(?:\.\d+)?) C\b/g, "$1 °C")
    .replace(/\b(fire_agent|wind_agent|heat_agent|brownout_agent|coordinator)\b/g, (m) => AGENT_WORDS[m]).replace(/line\(s\)/g, "lines");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
