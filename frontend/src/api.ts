export const API = (import.meta.env.VITE_API as string | undefined) ?? "http://localhost:8000";

/** Plans, co-pilot jobs and the situation brief: the shapes the backend returns for a planning run. */
export type Action = { type: string; target: string; mw?: number; pct?: number; targets?: string[] };
export type Plan = {
  id: string; title: string; actions: Action[];
  steps: { action: Action; why: string; raised_by: string[] }[];
  result: { is_safe: boolean; unserved_mw: number; customers_out: number; violations: string[]; critical_on_backup: string[]; n1_warnings: string[]; watch?: string[] };
  tradeoffs: string; confidence: number; assumptions: string[]; attempts: number; least_bad?: boolean;
};
export type CopilotOutput = {
  situation: string; mode: string; no_safe_option: boolean; plans: Plan[]; disagreements: string[];
  crew_staging?: { storm_index: number; alarm: number; above_alarm: boolean; pre_stage_crews_near: { line: string; name: string | null; p_damage: number }[] } | null;
  do_nothing: { violations: string[]; unserved_mw: number; customers_out: number; critical_on_backup: string[]; n1_warnings: string[] };
  brief?: Brief | null;
};
/** Shared situation brief (backend/agents/brief.py): one deterministic page of facts every agent starts from. */
export type BriefLine = {
  id: string; name: string | null; kv: number | null; zone: string | null; risk: number | null;
  gust_ms: number | null; rh_pct: number | null; people_downwind: number | null; hospitals_downwind: number | null;
};
export type Brief = {
  version: number; event: string; t_utc: string; scenario: string;
  fire: {
    line_alarm: number | null; region_index: number | null; region_alarm: number | null; n_over_alarm: number;
    shut_off_candidates: BriefLine[]; patrol_corridors: BriefLine[];
  };
  storm: {
    index: number | null; alarm: number | null; above_alarm: boolean;
    lines_damage_likely: { id: string; name: string | null; kv: number | null; zone: string | null; p_damage: number | null; gust_ms: number | null }[];
  } | null;
  grid: { load_mw: number | null; unserved_mw: number | null; n_violations: number; overloaded: { id: string; loading_pct: number | null }[]; critical_on_backup: string[] };
  weather: { max_gust_ms: number | null; min_rh_pct: number | null; max_temp_c: number | null; redflag_zones_active: string[] };
  site: { line: string; line_summary: string | null; worst_photo: number | null; disagreements: string[]; source: string | null }[];
  trust: { check: string; status: string | null; headline: string | null }[];
  headlines: string[];
};
export type LogEntry = { t: number; agent: string; team: string; message: string };
export type Job = { status: "running" | "done" | "error"; log: LogEntry[]; output: CopilotOutput | null; error?: string };
