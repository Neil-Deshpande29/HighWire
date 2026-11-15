import { useEffect, useMemo, useRef, useState } from "react";
import type { Job, LogEntry } from "../api";

/** What the agents are doing, live. Drawn only from the run's structured events (backend/agents/tools.py, EVENT_KINDS):
 *  nothing here is guessed. It starts as the orchestrator and the shared files. The orchestrator's routing decision
 *  appears, the agents it called come in, each one's findings travel into the files, the orchestrator reads them back,
 *  and then the physics check and the plans appear. An agent that was called but never started, or that never wrote its
 *  findings, stays visibly wrong, so this is also where to look when a run misbehaves. */
export type NodeId = string; // an agent id, "coordinator", "physics", a plan id, or a file path ("/findings/fire_agent.json")

const NAME: Record<string, string> = { fire_agent: "Fire", wind_agent: "Wind", heat_agent: "Heat", brownout_agent: "Brownout", buildings_agent: "Buildings" };
const TOOL_WORDS: Record<string, string> = {
  get_grid_summary: "reading the grid", get_site_context: "reading the ground", get_storm_damage: "reading storm damage", get_weather: "reading the weather",
  get_critical_sites: "checking critical sites", get_building_impact: "checking buildings", get_candidate_actions: "listing possible moves",
  simulate_plan: "testing a plan", ls: "listing the files", send_alert: "sending a text alert", get_operator_feedback: "reading your feedback",
};
const W = 316, ORCH_W = 168, TOP_H = 44, FILE_H = 12, AH = 46, PH = 40, QH = 30, RAIL = -9;
const STEP_MIN = 190, STEP_MAX = 650; // ms between events on screen: slow enough to follow, quick enough for a long run

type Ev = LogEntry & { kind: string; meta: Record<string, unknown> };
const isEv = (e: LogEntry): e is Ev => !!e.kind;
const still = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Everything the graph shows, from the events seen so far. */
function read(events: Ev[]) {
  const route: Record<string, { called: boolean; reason: string }> = {};
  const agents: { id: string; done: boolean; wrote: boolean; tool: string | null; findings: number | null }[] = [];
  const files: { path: string; by: string }[] = [];
  const readPaths = new Set<string>();
  let physics: { screened?: number; safe?: number } | null = null, verified = 0, alerts = 0, orchTool: string | null = null;
  let plans: { id: string; safe: boolean }[] | null = null;
  const agent = (id: string) => agents.find((a) => a.id === id);
  for (const e of events) {
    const m = e.meta, id = String(m.agent ?? e.agent);
    if (e.kind === "route") route[id] = { called: !!m.called, reason: String(m.reason ?? "") };
    else if (e.kind === "spawn") { if (!agent(id)) agents.push({ id, done: false, wrote: false, tool: null, findings: null }); }
    else if (e.kind === "tool") {
      if (m.tool === "send_alert") alerts++;
      const a = agent(e.agent);
      if (a) a.tool = String(m.tool); else orchTool = String(m.tool);
    } else if (e.kind === "vfs_write") {
      const path = String(m.path);
      if (!files.some((f) => f.path === path)) files.push({ path, by: id });
      const a = agent(id);
      if (a) a.wrote = true;
    } else if (e.kind === "vfs_read") readPaths.add(String(m.path));
    else if (e.kind === "agent_done") { const a = agent(id); if (a) { a.done = true; a.findings = typeof m.findings === "number" ? m.findings : a.findings; } }
    else if (e.kind === "physics") { if (m.plan) verified++; else physics = { screened: m.screened as number, safe: m.safe as number }; }
    else if (e.kind === "plans") plans = (m.plans as { id: string; safe: boolean }[]) ?? [];
  }
  return { route, agents, files, readPaths, physics, verified, alerts, orchTool, plans };
}

export default function AgentGraph({ job, selectedPlan, focus, onFocus, onPlan }: {
  job: Job; selectedPlan: string | null; focus: NodeId | null; onFocus: (id: NodeId) => void; onPlan: (id: string) => void;
}) {
  const events = useMemo(() => job.log.filter(isEv), [job.log]);
  const running = job.status === "running", failed = job.status === "error";
  // the playhead: events come on screen one at a time, at about the pace they happened, even when several arrive at once
  const [shown, setShown] = useState(0);
  const first = useRef(events[0]?.t);
  useEffect(() => { // a new run starts the picture over
    if (events[0]?.t !== first.current) { first.current = events[0]?.t; setShown(0); }
  }, [events]);
  useEffect(() => {
    if (shown >= events.length) return;
    if (still()) { setShown(events.length); return; }
    const gap = shown === 0 ? 120 : Math.min(STEP_MAX, Math.max(STEP_MIN, 1000 * (events[shown].t - events[shown - 1].t)));
    const h = window.setTimeout(() => setShown((n) => Math.min(n + 1, events.length)), gap);
    return () => window.clearTimeout(h);
  }, [shown, events]);
  const seen = events.slice(0, shown);
  const g = useMemo(() => read(seen), [shown, events]); // eslint-disable-line react-hooks/exhaustive-deps
  const last = seen[seen.length - 1];
  const caughtUp = shown >= events.length, over = !running && caughtUp;

  // called but never started: only known to be missing once the run is over
  const ghosts = over ? Object.keys(g.route).filter((a) => g.route[a].called && !g.agents.some((x) => x.id === a)) : [];
  const row = [...g.agents.map((a) => ({ ...a, ghost: false })), ...ghosts.map((id) => ({ id, done: true, wrote: false, tool: null, findings: null, ghost: true }))];
  // top to bottom, nothing crossing: the orchestrator, the agents it called, the shared files they write into, the
  // physics check, the plans. The orchestrator reads the files back along the rail down the left edge.
  const mid = W / 2, orchX = mid - ORCH_W / 2;
  const gap = 5, aw = row.length ? Math.min(74, (W - gap * (row.length - 1)) / row.length) : 0;
  const ax = (k: number) => (W - (row.length * aw + (row.length - 1) * gap)) / 2 + k * (aw + gap);
  const AY = TOP_H + 30;
  const FY = row.length ? AY + AH + 24 : TOP_H + 24;
  const fileRows = Math.max(1, Math.ceil(g.files.length / 2));
  const filesH = 22 + fileRows * FILE_H + 6;
  const fileAt = (path: string): [number, number] => {
    const k = Math.max(0, g.files.findIndex((f) => f.path === path));
    return [12 + (k % 2) * (W / 2 - 6), FY + 20 + (Math.floor(k / 2) + 1) * FILE_H - 3];
  };
  const showPhysics = !!g.physics || g.verified > 0 || !!g.plans;
  const PY = FY + filesH + 22;
  const plans = g.plans ?? [];
  const QY = PY + PH + 22, qw = Math.min(96, (W - 8 * Math.max(plans.length - 1, 0)) / Math.max(plans.length, 1));
  const qx = (k: number) => mid - (plans.length * qw + (plans.length - 1) * 8) / 2 + k * (qw + 8);
  const H = (g.plans ? QY + QH : showPhysics ? PY + PH : FY + filesH) + 6;
  const rail = `M0,${FY + 14} H${RAIL} V${TOP_H / 2} H${orchX}`; // files -> orchestrator, up the left edge

  // one packet in flight, for the event that just came on screen
  let packet: { path: string; phone?: boolean } | null = null;
  if (last && !still()) {
    const m = last.meta, k = row.findIndex((a) => a.id === String(m.agent ?? last.agent));
    if (last.kind === "vfs_write") {
      const [fx, fy] = fileAt(String(m.path));
      packet = { path: k >= 0 ? `M${ax(k) + aw / 2},${AY + AH} L${fx + 20},${fy - 4}` : `M${mid},${TOP_H} L${fx + 20},${fy - 4}` };
    } else if (last.kind === "vfs_read") packet = { path: rail };
    else if (last.kind === "tool" && m.tool === "send_alert") packet = { path: `M${orchX + ORCH_W - 10},6 L${orchX + ORCH_W + 34},-12`, phone: true };
  }
  const key = (id: NodeId, fn: (id: NodeId) => void) => (e: React.KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(id); } };
  const skipped = Object.keys(NAME).filter((a) => g.route[a] && !g.route[a].called);
  // before the first event the run still says something: which planner it is asking, and its latest line
  const said = job.log.filter((e) => !e.kind && (e.agent === "coordinator" || e.agent === "system"));
  const asking = said.some((e) => /^model /.test(e.message));
  const orchSub = failed ? "stopped" : !seen.length ? (asking ? "asking the AI team" : running ? "starting" : "idle") : g.plans ? `${plans.length} plan${plans.length === 1 ? "" : "s"} ready`
    : last?.kind === "route" ? "deciding who to call" : last?.kind === "vfs_read" ? "reading the findings" : g.orchTool && g.agents.every((a) => a.done) ? TOOL_WORDS[g.orchTool] ?? "working"
    : g.agents.some((a) => !a.done) ? "waiting for the agents" : "working";

  return (
    <div className="agraph">
      <svg className={`synth ${running || !caughtUp ? "running" : ""}`} viewBox={`${RAIL - 4} -16 ${W - RAIL + 8} ${H + 16}`} role="group" aria-label="What the agents are doing">
        {row.map((a, k) => (
          <path key={a.id} className={`edge appear ${a.ghost ? "idle" : a.done ? "used" : "flow"}`}
            d={`M${mid},${TOP_H} C${mid},${TOP_H + 18} ${ax(k) + aw / 2},${AY - 18} ${ax(k) + aw / 2},${AY}`} />
        ))}
        {row.map((a, k) => !a.ghost && <path key={`w${a.id}`} className={`edge ${a.wrote ? "used" : "idle"}`} d={`M${ax(k) + aw / 2},${AY + AH} V${FY}`} />)}
        {!row.length && <path className={`edge ${g.files.length ? "used" : "idle"}`} d={`M${mid},${TOP_H} V${FY}`} />}
        <path className={`edge ${g.readPaths.size ? "used" : "idle"}`} d={rail} />
        {showPhysics && <path className="edge used appear" d={`M${mid},${FY + filesH} V${PY}`} />}
        {plans.map((p, k) => (
          <path key={p.id} className={`edge drawn ${p.safe ? "safe" : "unsafe"}`} d={`M${mid},${PY + PH} C${mid},${PY + PH + 12} ${qx(k) + qw / 2},${QY - 12} ${qx(k) + qw / 2},${QY}`} />
        ))}

        <g className={`node ${failed ? "err" : over ? "done" : "busy"} ${focus === "coordinator" ? "focus" : ""}`} role="button" tabIndex={0}
          onClick={() => onFocus("coordinator")} onKeyDown={key("coordinator", onFocus)}>
          <title>The orchestrator decides which agents this hour needs, reads their findings from the shared files and builds the plans.</title>
          <rect x={orchX} y={0} width={ORCH_W} height={TOP_H} rx={10} className="box solid" />
          <text x={mid} y={18} className="n-name on-solid">Orchestrator</text>
          <text x={mid} y={33} className="n-sub on-solid">{orchSub}</text>
        </g>

        {row.map((a, k) => {
          const missing = a.ghost || (a.done && !a.wrote);
          const sub = a.ghost ? "never started" : !a.done ? (a.tool ? TOOL_WORDS[a.tool] ?? a.tool : "starting") : !a.wrote ? "no findings file" : a.findings != null ? `${a.findings} finding${a.findings === 1 ? "" : "s"}` : "done";
          return (
            <g key={a.id} className={`node appear ${a.ghost ? "wait" : a.done ? "done" : "busy"} ${missing ? "missing" : ""} ${focus === a.id ? "focus" : ""}`} role="button" tabIndex={0}
              onClick={() => onFocus(a.id)} onKeyDown={key(a.id, onFocus)}>
              <title>{`${NAME[a.id] ?? a.id} agent: ${sub}. Click to read what it wrote.`}</title>
              <rect x={ax(k)} y={AY} width={aw} height={AH} rx={8} className="box" />
              <text x={ax(k) + aw / 2} y={AY + 18} className="n-name">{NAME[a.id] ?? a.id}</text>
              <text x={ax(k) + aw / 2} y={AY + 33} className="n-sub">{sub.length > 15 && aw < 70 ? `${sub.slice(0, 13)}…` : sub}</text>
            </g>
          );
        })}

        <g className={`node done vfs ${focus?.startsWith("/") ? "focus" : ""}`}>
          <title>The shared files: every agent writes its findings here and the orchestrator reads them back.</title>
          <rect x={0} y={FY} width={W} height={filesH} rx={9} className="box folder" />
          <text x={10} y={FY + 15} className="n-name left">Shared files</text>
          <text x={W - 10} y={FY + 15} className="n-sub right">{g.files.length ? `${g.files.length} written · ${g.readPaths.size} read` : "empty"}</text>
          {g.files.map((f) => {
            const [fx, fy] = fileAt(f.path);
            return (
              <text key={f.path} x={fx} y={fy} role="button" tabIndex={0}
                className={`file left appear ${g.readPaths.has(f.path) ? "read" : ""} ${focus === f.path ? "picked" : ""} ${last?.meta.path === f.path ? "fresh" : ""}`}
                onClick={() => onFocus(f.path)} onKeyDown={key(f.path, onFocus)}>
                {f.path.replace(/^\/(findings|plan)\//, "").replace(/_agent/, "")}{g.readPaths.has(f.path) ? " ✓" : ""}
              </text>
            );
          })}
        </g>

        {showPhysics && (
          <g className={`node appear ${g.plans ? "done" : "busy"} ${focus === "physics" ? "focus" : ""}`} role="button" tabIndex={0} onClick={() => onFocus("physics")} onKeyDown={key("physics", onFocus)}>
            <title>Every option is run through the physics before it can reach you.</title>
            <rect x={mid - 88} y={PY} width={176} height={PH} rx={10} className="box check" />
            <text x={mid} y={PY + 16} className="n-name">Physics check</text>
            <text x={mid} y={PY + 30} className="n-sub">
              {g.physics?.screened != null ? `${g.physics.screened} option${g.physics.screened === 1 ? "" : "s"} tested · ${g.physics.safe} safe` : g.verified ? `${g.verified} plan${g.verified === 1 ? "" : "s"} re-checked` : "checking"}
            </text>
          </g>
        )}
        {g.plans && plans.length === 0 && <text x={mid} y={QY + 14} className="n-sub">no switching needed</text>}
        {plans.map((p, k) => (
          <g key={p.id} className={`node appear done plan ${p.safe ? "safe" : "unsafe"} ${selectedPlan === p.id ? "focus" : ""}`} role="button" tabIndex={0}
            onClick={() => onPlan(p.id)} onKeyDown={key(p.id, onPlan)}>
            <title>{`Plan ${p.id.replace(/^P/, "")}: ${p.safe ? "passes" : "fails"} the physics check. Click to see it on the map.`}</title>
            <rect x={qx(k)} y={QY} width={qw} height={QH} rx={8} className="box" />
            <text x={qx(k) + qw / 2} y={QY + 19} className="n-name">Plan {p.id.replace(/^P/, "")} {p.safe ? "✓" : "✕"}</text>
          </g>
        ))}

        {packet && (
          <g key={shown} className="packet">
            {packet.phone ? <text className="phone" x={0} y={0}>📱</text> : <circle r={4.5} />}
            <animateMotion dur="0.55s" fill="freeze" path={packet.path} />
          </g>
        )}
      </svg>
      {running && !seen.length && said.length > 0 && <p className="route-note">{said[said.length - 1].message.slice(0, 140)}</p>}
      {skipped.length > 0 && (
        <p className="route-note">Not called: {skipped.map((a) => `${NAME[a]} (${g.route[a].reason || "not needed"})`).join("; ")}</p>
      )}
      {g.alerts > 0 && <p className="route-note">📱 {g.alerts} text alert{g.alerts === 1 ? "" : "s"} asked for by the orchestrator</p>}
      {!running && events.length > 0 && (
        <button className="link small" disabled={!caughtUp} onClick={() => setShown(0)}>{caughtUp ? "Replay" : "Playing…"}</button>
      )}
    </div>
  );
}
