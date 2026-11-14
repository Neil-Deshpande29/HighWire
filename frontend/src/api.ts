export const API = (import.meta.env.VITE_API as string | undefined) ?? "http://localhost:8000";

export type Weather = {
  air_temp_c: number; wind_speed_ms: number; wind_dir_deg: number; gust_ms: number;
  humidity_pct: number; solar_w_m2: number; flood_level_m: number;
};
export type Bus = {
  id: string; name: string; x: number; y: number; zone: string; load_mw: number; critical: boolean;
  critical_type: "hospital" | "water" | "none"; backup_hours: number; elevation_m: number; flood_threshold_m: number;
};
export type Line = {
  id: string; from_bus: string; to_bus: string; zone: string; status: "open" | "closed"; rating_amps: number;
  max_temp_c: number; length_m: number; tie: boolean;
};
export type LineSim = {
  id: string; status: string; energized: boolean; flow_mw: number; amps: number; loading_pct: number; temp_c: number;
  sag_m: number; clearance_m: number; blowout_m: number; swing_deg: number; fire_risk: number; violations: string[];
};
export type BusSim = {
  id: string; served_mw: number; unserved_mw: number; shed_mw: number; flooded: boolean; energized: boolean;
  deenergized: boolean; on_backup: boolean;
};
export type Tree = { line: string; x: number; y: number; height_m: number; lateral_offset_m: number };
export type Report = {
  id: string; text: string; kind: string; severity: number; x: number; y: number; line: string | null;
  bus: string | null; corroborated: boolean; snapped_to: string; parser: string;
};
export type Sim = {
  lines: Record<string, LineSim>; buses: Record<string, BusSim>; gens: Record<string, number>;
  totals: { unserved_mw: number; customers_out: number; critical_on_backup: string[]; violation_count: number; load_mw: number };
  violations: string[]; n1_warnings: string[]; is_safe: boolean;
};
export type Bulletin = { timestamp: string; plan_id: string; text: string; safety: string };
export type GridView = {
  scenario: string; scenario_names: string[]; scenario_description: string; weather: Record<string, Weather>; load_multiplier: number;
  buses: Bus[]; lines: Line[]; trees: Tree[]; sim: Sim; reports: Report[];
  plan_status: Record<string, string>; bulletin: Bulletin | null; mode: "llm" | "mock";
};
export type Terrain = { n: number; size_m: number; sea_x_m: number; heights: number[][] };

export type Action = { type: string; target: string; mw?: number };
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
};
export type LogEntry = { t: number; agent: string; team: string; message: string };
export type Job = { status: "running" | "done" | "error"; log: LogEntry[]; output: CopilotOutput | null; error?: string };

async function req<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(API + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  state: () => req<GridView>("/state"),
  terrain: () => req<Terrain>("/terrain"),
  scenario: (name: string) => req<GridView>("/scenario", "POST", { name }),
  toggle: (id: string) => req<GridView>(`/lines/${id}/toggle`, "POST"),
  report: (text: string, x?: number, y?: number) => req<{ report: Report; state: GridView }>("/reports", "POST", { text, x, y }),
  reset: () => req<GridView>("/reset", "POST"),
  run: () => req<{ job_id: string }>("/copilot/run", "POST"),
  job: (id: string) => req<Job>(`/copilot/${id}`),
  approve: (id: string) => req<{ bulletin: Bulletin; state: GridView }>(`/plans/${id}/approve`, "POST"),
  reject: (id: string) => req<{ plan_status: Record<string, string> }>(`/plans/${id}/reject`, "POST"),
};
