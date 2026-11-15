import { API, type Action, type Authority, type BuildingsOut, type Job, type Plan, type Severity } from "../api";

export type LAEvent = {
  id: string; name: string; kind: "wind" | "heat" | "control" | "forecast"; split: "train" | "test" | "none"; fetched_utc?: string;
  start: string; end: string; has_replay: boolean; fires: string[];
  v4a: { lead_time_h: number | null; spearman_la: number; first_alarm_utc: string | null; first_spike_utc: string | null } | null;
};
export type Alerts = {
  on: boolean; dry_run: boolean; missing: string[]; to: string | null;
  alerts: { n: number; at_utc: string; message: string; source: string; status: string; why: string }[];
};
export type Branch = {
  id: string; kv: number; kv_class: number; cable: boolean; name: string | null; zone: string;
  from_bus: string; to_bus: string; length_km: number; rating_a: number; geometry: [number, number][];
};
export type Fire = {
  id: string; name: string; event: string; lat: number; lon: number; ignition_utc: string;
  utility_related: string; facility: string; source_url: string; perimeter: [number, number][] | null;
};
export type LAStatic = {
  bbox: { lat_min: number; lat_max: number; lon_min: number; lon_max: number };
  proj: { lat0: number; lon0: number }; modeled_note: string;
  terrain: { lats: number[]; lons: number[]; elev: number[][] };
  branches: Branch[];
  buses: { id: string; name: string; lat: number; lon: number; kv: number; kind: string }[];
  labels: { id: string; name: string; lat: number; lon: number; kv: number }[];
  critical_sites: { name: string; type: string; lat: number; lon: number; bus: string }[];
  redflag_zones: Record<string, [number, number][][]>;
  fires: Fire[];
  calibration: { fire: { g50: number; h50: number; c50: number; line_alarm: number; region_alarm: number };
                 storm: { onset: number; tau: number; rain_tau: number; alarm: number } | null };
};
export type Hour = {
  event: string; i: number; n: number; t_utc: string; t_local: string;
  lines: { risk: number[]; loading: number[]; temp: number[]; clear: number[]; gust: number[]; rh: number[]; flow: number[] };
  region_index: number; region_alarm: number; line_alarm: number; load_mw: number;
  demand: { LDWP: number; SCE: number }; outages: Record<string, number | null>;
  over_alarm: TopLine[];
  n_over_alarm: number; max_loading: number; n_overloaded: number;
  weather: { lats: number[]; lons: number[]; gust: number[][]; rh: number[][]; temp: number[][]; dir: number[][]; max_gust: number; min_rh: number; max_temp: number };
  redflag_active: string[]; fires: { id: string; name: string; ignited: boolean; hours_since_ignition: number }[];
  storm: Storm | null; line_damage: number[] | null;
  source: string;
  copilot: LAView | null;
};
export type Storm = {
  index: number; alarm: number; wind_hazard_pct: number; rain_hazard_pct: number; n_lines_damage_likely: number; max_precip_mm_h: number;
  top_lines: { id: string; name: string | null; kv: number; zone: string; p_damage: number; gust: number }[];
};
export type CrewStaging = { storm_index: number; alarm: number; above_alarm: boolean; pre_stage_crews_near: { line: string; name: string | null; p_damage: number }[] };
export type LAView = {
  event: string; t_utc: string; scenario: string; opened: string[]; deenergized: string[];
  totals: { unserved_mw: number; customers_out: number; critical_on_backup: string[]; violation_count: number; load_mw: number };
  violations: string[]; n_violations: number;
  lines: Record<string, { status: string; fire_risk: number; loading_pct: number; energized: boolean }>;
  plan_status: Record<string, string>; bulletin: { text: string } | null;
};
export type Series = {
  event: string; t_utc: string[]; risk_index: number[]; storm_index: number[] | null; storm_alarm: number | null; la_out: (number | null)[]; four_county_out: (number | null)[];
  load_mw: number[]; max_loading: number[]; region_alarm: number;
  v4a: { lead_time_h: number | null; first_alarm_utc: string | null; first_spike_utc: string | null; outage_baseline_la: number;
         spearman_la: number; exploratory_lead_to_main_surge_h: number | null } | null;
  fires: Fire[]; source: string; now_utc?: string | null;
  n_high_risk: number[]; n_overloaded: number[]; fire_checks: FireCheck[];
};
/** V5 ignition case + when the model first flagged the line nearest the origin (from the replay file). */
export type FireCheck = { fire: string; ignition_utc: string; nearest_line: string; kv: number; distance_km: number; first_flag_utc: string | null;
  hours_before: number | null; top_pct: number; n_lines: number; split: string; caveat: string | null;
  facility: string | null; substation: { id: string; name: string } | null; named_in_report?: boolean };
export type Conditions = { gust_pct: number; rh_pct: number; temp_c: number; demand_pct: number };
export const NO_CONDITIONS: Conditions = { gust_pct: 0, rh_pct: 0, temp_c: 0, demand_pct: 0 };
/** Vegetation within `corridor_m` of a line's wires, from its aerial photos (site_agent/greenery.py). */
export type Greenery = { share: number; level: "low" | "medium" | "high"; photos: number; corridor_m: number; method: string; source: string; photo_date: string | null };
export type TopLine = { id: string; name: string | null; kv: number; zone: string; risk: number; gust: number; rh: number;
  downwind?: { people: number; hospitals: number; water_plants: number; sites: string[]; toward_deg: number };
  greenery?: Greenery | null; trees?: Trees | null; if_cut?: BuildingsOut & { customers: number } };
/** Trees within `corridor_m` of a line's wires, counted on Vexcel aerial imagery (backend/la/trees.py). */
export type Trees = { trees: number; spots: number; corridor_m: number; source: string; many: boolean };
/** GET /la/buildings: the building records in use and the priority ladder they fill. */
export type BuildingRecords = {
  sources: string[]; n: number; costar: number; homes: number; by_kind: Record<string, number>;
  ladder: { tier: number; name: string; why: string; kinds: string[]; n: number; names: string[] }[];
};
export type Feedback = { n: number; reasons: { id: string; text: string; n: number }[]; notes: string[]; choices?: Record<string, string> };
/** POST /la/simulate: physics for one moment + actions (+ Study conditions), never touching the Operate state. */
export type SimResult = {
  is_safe: boolean; unserved_mw: number; customers_out: number; critical_on_backup: string[]; hospitals_on_backup: number;
  critical_names: (string | null)[]; buildings_out?: BuildingsOut; violations: string[]; n_violations: number; watch: string[];
  lines: { risk: number[]; loading: number[]; energized: boolean[]; gust: number[]; rh: number[]; temp: number[] };
  buses_out: string[]; bus_load: Record<string, number>;
  situation: { n_high_risk: number; max_loading: number; n_overloaded: number; load_mw: number; max_gust: number; min_rh: number;
               max_temp: number; people_downwind: number; hospitals_downwind: number; water_plants_downwind: number };
  top_lines: TopLine[]; conditions: Conditions; t_utc: string;
  fire_exposure: FireExposure; authority: Authority;
  /** what the cut costs the customers it reaches, in dollars, for an 8-hour and a 24-hour shut-off (backend/la/costs.py) */
  cost?: { customers: number; usd: Record<string, number>; business_share: number; source: string };
};
/** Buildings downwind of the lines still live at high fire risk (backend/la/exposure.py): the cost of not acting. */
export type FireExposure = {
  by_kind: Record<string, number>; score: number; hospitals: number; n: number; critical: number; homes: number; severity: Severity | null;
  sites: { name: string; kind: string; source: string; severity: Severity; line: string; distance_km: number; units?: number | null }[];
};
type Reading = { gust_ms: number | null; wind_ms: number | null; rh_pct: number | null; temp_c: number | null };
/** GET /la/sensors: what each weather station measured against the weather the model ran on. */
export type Station = {
  id: string; name: string; lat: number; lon: number; at_utc: string | null; measured: Reading | null; model: Reading;
  gust_diff_ms: number | null; rh_diff_pct: number | null; flag: "agrees" | "model_low" | "model_high" | "no_report"; drier: boolean;
};
export type Sensors = {
  mode: "live" | "recorded"; source: string; t_utc: string; stations: Station[]; n: number; reporting: number; agree: number;
  model_low: string[]; model_high: string[]; drier: string[]; level: "ok" | "warn" | "none"; tolerance: { gust_ms: number; rh_pct: number };
};
/** POST /la/risk: each high-risk line's likelihood and its two consequences, never added together. */
export type Band = "high" | "medium" | "low";
export type RiskRow = {
  id: string; name: string | null; kv: number; zone: string; class: "sub" | "bulk"; index: number; ratio: number;
  gust_ms: number; rh_pct: number; clearance_m: number; loading_pct: number; downwind: Downwind; exposure_band: Band;
  if_cut: (BuildingsOut & { customers: number }) | null; greenery: Greenery | null; trees: Trees | null;
};
export type RiskRegister = {
  t_utc: string; alarm: number; elevated_from: number; n_high: number; n_elevated: number; rows: RiskRow[];
  matrix: { high: Record<Band, string[]>; elevated: Record<Band, number> };
  bands: { people_high: number; people_medium: number; cone_km: number };
};
/** GET /la/record: the append-only decision record. */
export type RecordEntry = { n: number; at_utc: string; kind: string; role: string; text: string; event: string | null; hour_utc: string | null; data: Record<string, unknown> };
export type Role = "operator" | "supervisor";
export type V5Case = { fire: string; event: string; nearest_line: string; n_lines: number; model_top_pct: number; gust_only_top_pct: number; split: string };
export type Downwind = { people: number; hospitals: number; water_plants: number; sites: string[]; toward_deg: number };
export type SitePoint = {
  lat: number; lon: number; elev_m: number; zone: string; landfire_fuel?: string; spark_receptivity?: number; veg_height_m?: number;
  weather?: { gust_ms: number; rh_pct: number; wind_from_deg: number }; downwind?: Downwind;
};
export type PhotoLook = { land_cover: string[]; structures: string; vegetation_near_tower: string; wildland_urban_interface: boolean;
                          access: string; summary: string };
export type Site = {
  line: string;
  facts: {
    line: string; name: string | null; kv: number; zone: string; length_km: number; n_towers: number; points: SitePoint[];
    fuel_mix?: Record<string, number>; burnable_share?: number; max_veg_height_m?: number; downwind?: Downwind;
  };
  photos: { n: number; point: SitePoint; tile: { item: string; photo_date: string; bbox: number[]; source: string } | null;
            look: PhotoLook | null; checks: { kind: string; message: string }[] }[];
  look: { line_summary: string; confidence: string; worst_photo: number | null } | null;
  look_source: string | null;
  greenery?: Greenery | null;
  checks: { kind: string; message: string; photo: number }[];
  notes: string[];
  disclaimer: string;
};
/** GET /la/imagery: the stitched aerial picture under the map, if one has been built. */
export type Imagery = {
  available: boolean; source: string; built_utc?: string; zoom?: number; coverage?: number; fallback?: string | null;
  /** the wider picture of the land and sea around the region, when it has been built */
  surround?: { bbox: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }; built_utc: string } | null;
};
export type Card = { id: string; title: string; status: string; headline: string; baseline: string; plot: string | null; one_liner: string };

async function get<T>(p: string): Promise<T> {
  const r = await fetch(API + p);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}
async function post<T>(p: string, body?: unknown): Promise<T> {
  const r = await fetch(API + p, { method: "POST", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail ?? m; } catch { /* */ } throw new Error(m); }
  return r.json();
}

export const la = {
  events: () => get<LAEvent[]>("/events"),
  static: () => get<LAStatic>("/la/static"),
  hour: (eid: string, i: number) => get<Hour>(`/replay/${eid}?i=${i}`),
  series: (eid: string) => get<Series>(`/la/series/${eid}`),
  validation: () => get<{ cards: Card[]; calibration: unknown }>("/validation"),
  run: (event: string, t: string, actions: Action[] = [], conditions?: Conditions) =>
    post<{ job_id: string; state: LAView; study: boolean }>("/la/copilot/run", { event, t, actions, conditions }),
  simulate: (event: string, t: string, actions: Action[] = [], conditions?: Conditions) =>
    post<SimResult>("/la/simulate", { event, t, actions, conditions }),
  // validation JSON is written by Python and may contain NaN, which JSON.parse rejects
  result: async <T,>(file: string): Promise<T> => {
    const r = await fetch(`${API}/results/${file}`);
    if (!r.ok) throw new Error(r.statusText);
    return JSON.parse((await r.text()).replace(/\bNaN\b/g, "null")) as T;
  },
  job: (id: string) => get<Job>(`/copilot/${id}`),
  approve: (id: string, role: Role = "operator") => post<{ bulletin: { text: string }; state: LAView }>(`/la/plans/${id}/approve`, { role }),
  reject: (id: string, reasons: string[] = [], note = "", role: Role = "operator") =>
    post<{ plan_status: Record<string, string>; feedback: Feedback }>(`/la/plans/${id}/reject`, { role, reasons, note }),
  feedback: () => get<Feedback>("/la/feedback"),
  buildings: () => get<BuildingRecords>("/la/buildings"),
  cut: (event: string, t: string, line: string, actions: Action[] = []) =>
    post<BuildingsOut & { customers: number }>("/la/cut", { event, t, line, actions }),
  escalate: (id: string) => post<{ plan_status: Record<string, string>; authority: Authority }>(`/la/plans/${id}/escalate`),
  record: () => get<{ entries: RecordEntry[]; basis: string }>("/la/record"),
  reset: () => post<{ entries: RecordEntry[] }>("/la/reset"),
  /** Text alerts (backend/notify.py): what went out or would have, a test text, and the on/off switch. */
  alerts: () => get<Alerts>("/alerts"),
  alertsTest: () => post<Alerts>("/alerts/test"),
  alertsSwitch: (on: boolean) => post<Alerts>("/alerts/switch", { on }),
  /** The LED strip, if one is plugged in: tell the server which hour is on screen and what was switched by hand.
   *  The server works out the scene and changes the strip itself when a plan is approved (backend/routes_la.py). */
  lights: (event: string, t: string, actions: Action[]) => post<{ connected: boolean }>("/lights", { event, t, actions }),
  sensors: (eid: string, t: string) => get<Sensors>(`/la/sensors/${eid}?t=${encodeURIComponent(t)}`),
  risk: (event: string, t: string, actions: Action[] = [], conditions?: Conditions) =>
    post<RiskRegister>("/la/risk", { event, t, actions, conditions }),
  refreshForecast: () => post<{ status: string; error: string | null }>("/la/forecast/refresh"),
  // the site route takes replay events only; on the live forecast ask for the line's photos and facts without an hour
  site: (line: string, eid: string, i: number) => get<Site>(eid === "FX" ? `/la/site/${line}` : `/la/site/${line}?event=${eid}&i=${i}`),
  forecastStatus: () => get<{ status: string; error: string | null; summary: unknown }>("/la/forecast/status"),
  imagery: () => get<Imagery>("/la/imagery"),
};
export const imageryUrl = (version: string) => `${API}/la/imagery/basemap.jpg?v=${encodeURIComponent(version)}`;
export const surroundUrl = (version: string) => `${API}/la/imagery/surround.jpg?v=${encodeURIComponent(version)}`;
export const resultUrl = (f: string) => `${API}/results/${f}`;
export const siteImageUrl = (line: string, n: number, item: string) => `${API}/la/site/${line}/photo/${n}.jpg?v=${item}`;
export type { Action, Authority, Job, Plan };
