export const API = (import.meta.env.VITE_API as string | undefined) ?? "http://localhost:8000";

/** Plans, co-pilot jobs and the situation brief: the shapes the backend returns for a planning run. */
export type Action = { type: string; target: string; mw?: number; pct?: number; targets?: string[] };
/** Buildings behind cut-off substations (backend/la/buildings.py): counts by kind, a triage score, the heaviest names. */
export type Severity = "high" | "medium" | "low";
export type BuildingsOut = {
  by_kind: Record<string, number>; score: number; n: number; hospitals?: number; severity?: Severity | null;
  /** `critical` counts tiers 1 to 3 (hospitals, water plants, medical sites, senior housing, schools); `homes` is apartment homes from CoStar. */
  critical?: number; homes?: number; ladder?: { tier: number; name: string; n: number }[];
  sites: { name: string; kind: string; bus: string; backup_hours: number | null; source: string; severity?: Severity; units?: number | null }[];
};
/** Who may approve (backend/la/authority.py): the operator alone, only with a supervisor, or nobody (fails physics). */
export type Authority = { level: "operator" | "supervisor" | "blocked"; reasons: string[]; basis: string };
export type Plan = {
  id: string; title: string; actions: Action[]; authority?: Authority; from_feedback?: boolean;
  steps: { action: Action; why: string; raised_by: string[]; raised_from?: string[]; buildings?: BuildingsOut }[];
  result: { is_safe: boolean; unserved_mw: number; customers_out: number; violations: string[]; critical_on_backup: string[]; n1_warnings: string[]; watch?: string[];
            hospitals_on_backup?: number; buildings_out?: BuildingsOut };
  tradeoffs: string; confidence: number; assumptions: string[]; attempts: number; least_bad?: boolean;
};
export type CopilotOutput = {
  situation: string; mode: string; no_safe_option: boolean; plans: Plan[]; disagreements: string[];
  crew_staging?: { storm_index: number; alarm: number; above_alarm: boolean; pre_stage_crews_near: { line: string; name: string | null; p_damage: number }[] } | null;
  do_nothing: { violations: string[]; unserved_mw: number; customers_out: number; critical_on_backup: string[]; n1_warnings: string[] };
  brief?: Brief | null;
  fallback_reason?: string | null;
  /** Rule-based planner only: what each agent flagged, with the physical values it read (computed by code). */
  agent_findings?: Record<string, { target: string; issue: string; evidence: string; severity: number }[]>;
  /** What the coordinator did with the operator's earlier rejections. */
  feedback_used?: { reason: string; text: string; applied: string }[];
  /** How the team's work came together (backend/agents/synthesis.py), drawn as a small graph. */
  synthesis?: Synthesis;
  /** The run's shared files at the end (path -> text). */
  vfs?: Record<string, string>;
};
export type Synthesis = {
  mode: string;
  /** One overall confidence figure for the run, worked out by code (backend/agents/synthesis.py). Not a probability. */
  confidence?: { score: number; level: "high" | "medium" | "low"; reasons: string[] };
  agents: { id: string; name: string; findings: number; act_now: number; log_lines: number; raised_steps: number }[];
  coordinator: { candidates: number | null; disagreements: number; feedback: number };
  physics: { screened: number | null; safe: number | null; verified: number };
  plans: { id: string; safe: boolean; from_feedback: boolean; raised_by: string[] }[];
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
/** One line of a run's log. A line may also be a structured event (`kind`, `meta`): the agent graph is drawn from those. */
export type LogEntry = { t: number; agent: string; team: string; message: string; kind?: string; meta?: Record<string, unknown> };
/** `vfs` is the run's shared files so far (path -> text): the agents' findings and the orchestrator's dispatch note. */
export type Job = { status: "running" | "done" | "error"; log: LogEntry[]; output: CopilotOutput | null; error?: string; vfs?: Record<string, string> };
