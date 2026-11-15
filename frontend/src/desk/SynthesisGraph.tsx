import type { Job } from "../api";
import type { AgentChip } from "../ui/TeamPanel";

/** How the team's work comes together, live. Agents across the top report down to the coordinator, the coordinator's
 *  options go through the physics check, and plans come out at the bottom. While the team is working the lines flow
 *  and the busy node pulses; when it is done every node carries its numbers. Click a node to read what it found.
 *  Everything drawn here comes from the run's own log and output (backend/agents/synthesis.py), never from a model. */
export type NodeId = string; // an agent id, "coordinator", "physics", or a plan id

const SHORT: Record<string, string> = { fire_agent: "Fire", wind_agent: "Wind", heat_agent: "Heat", brownout_agent: "Brownout", buildings_agent: "Buildings" };
const W = 316, AH = 52, CY = 96, CH = 46, PY = 170, PH = 46, QY = 246, QH = 30, H = QY + QH + 4;

export default function SynthesisGraph({ job, chips, shownPlans, selectedPlan, focus, onFocus, onPlan }: {
  job: Job; chips: AgentChip[]; shownPlans: string[]; selectedPlan: string | null; focus: NodeId | null;
  onFocus: (id: NodeId) => void; onPlan: (id: string) => void;
}) {
  const out = job.output, syn = out?.synthesis, running = job.status === "running", failed = job.status === "error";
  const said = (agent: string) => job.log.filter((e) => e.agent === agent);
  const gap = 4, aw = (W - gap * (chips.length - 1)) / chips.length;
  const ax = (k: number) => k * (aw + gap);
  const mid = W / 2;
  const agents = chips.map((c, k) => {
    const found = out?.agent_findings?.[c.id] ?? [];
    const lines = said(c.id).length;
    const state = failed ? "err" : !running ? "done" : lines ? "busy" : "wait";
    const raised = syn?.agents.find((a) => a.id === c.id)?.raised_steps ?? 0;
    return { id: c.id, name: SHORT[c.id] ?? c.name, k, state, n: found.length, urgent: found.filter((f) => f.severity >= 3).length, lines, raised };
  });
  const coordLines = said("coordinator");
  const candidates = syn?.coordinator.candidates ?? Number(coordLines.map((e) => e.message.match(/^(\d+) candidate actions/)?.[1]).find(Boolean) ?? NaN);
  const screened = job.log.map((e) => (e.agent === "physics" ? e.message.match(/^screened (\d+) combinations \(\d+ simulations\): (\d+) safe/) : null)).find(Boolean);
  const tested = syn?.physics.screened ?? (screened ? Number(screened[1]) : null), safe = syn?.physics.safe ?? (screened ? Number(screened[2]) : null);
  const coordState = failed ? "err" : !running ? "done" : coordLines.some((e) => /candidate|violations to resolve|screening/.test(e.message)) ? "busy" : "wait";
  const physState = failed ? "err" : !running ? "done" : screened ? "done" : coordLines.some((e) => /screening/.test(e.message)) ? "busy" : "wait";
  const plans = (syn?.plans ?? []).filter((p) => shownPlans.includes(p.id));
  const qw = Math.min(96, (W - 8 * Math.max(plans.length - 1, 0)) / Math.max(plans.length, 1));
  const qx = (k: number) => mid - (plans.length * qw + (plans.length - 1) * 8) / 2 + k * (qw + 8);
  const node = (id: NodeId, state: string) => `node ${state} ${focus === id ? "focus" : ""}`;
  const key = (id: NodeId) => (e: React.KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onFocus(id); } };
  return (
    <svg className={`synth ${running ? "running" : ""}`} viewBox={`0 0 ${W} ${H}`} role="group" aria-label="How the agents' findings become plans">
      {agents.map((a) => (
        <path key={a.id} className={`edge ${a.state === "busy" ? "flow" : ""} ${a.state === "done" && a.raised ? "used" : ""} ${a.state === "wait" ? "idle" : ""}`}
          d={`M${ax(a.k) + aw / 2},${AH} C${ax(a.k) + aw / 2},${AH + 26} ${mid},${CY - 26} ${mid},${CY}`} />
      ))}
      <path className={`edge ${physState === "busy" ? "flow" : ""} ${physState === "wait" ? "idle" : "used"}`} d={`M${mid},${CY + CH} L${mid},${PY}`} />
      {plans.map((p, k) => (
        <path key={p.id} className={`edge drawn ${p.safe ? "safe" : "unsafe"}`} d={`M${mid},${PY + PH} C${mid},${PY + PH + 16} ${qx(k) + qw / 2},${QY - 16} ${qx(k) + qw / 2},${QY}`} />
      ))}

      {agents.map((a) => (
        <g key={a.id} className={node(a.id, a.state)} role="button" tabIndex={0} onClick={() => onFocus(a.id)} onKeyDown={key(a.id)}>
          <title>{a.state === "done" ? `${a.name} agent: ${a.n} flagged, ${a.urgent} urgent. Click to read them.` : `${a.name} agent: ${a.state === "busy" ? "working" : "waiting"}`}</title>
          <rect x={ax(a.k)} y={0} width={aw} height={AH} rx={8} className={a.urgent ? "box urgent" : "box"} />
          <text x={ax(a.k) + aw / 2} y={15} className="n-name">{a.name}</text>
          <text x={ax(a.k) + aw / 2} y={35} className="n-big">{a.state === "done" ? a.n : a.state === "busy" ? "…" : "·"}</text>
          <text x={ax(a.k) + aw / 2} y={46} className="n-sub">{a.state === "done" ? (a.urgent ? `${a.urgent} urgent` : "flagged") : a.state === "busy" ? "reading" : a.state === "err" ? "stopped" : "waiting"}</text>
        </g>
      ))}

      <g className={node("coordinator", coordState)} role="button" tabIndex={0} onClick={() => onFocus("coordinator")} onKeyDown={key("coordinator")}>
        <title>The coordinator weighs every agent's findings and builds the options. Click to read what it decided.</title>
        <rect x={mid - 82} y={CY} width={164} height={CH} rx={10} className="box solid" />
        <text x={mid} y={CY + 18} className="n-name on-solid">Coordinator</text>
        <text x={mid} y={CY + 34} className="n-sub on-solid">
          {coordState === "wait" ? "waiting for the agents" : Number.isFinite(candidates) ? `${candidates} possible move${candidates === 1 ? "" : "s"} weighed` : coordState === "done" ? "found nothing to switch" : "weighing the findings"}
          {syn?.coordinator.feedback ? " · used your feedback" : ""}
        </text>
      </g>

      <g className={node("physics", physState)} role="button" tabIndex={0} onClick={() => onFocus("physics")} onKeyDown={key("physics")}>
        <title>Every option is run through the physics before it can reach you. Click for the details.</title>
        <rect x={mid - 82} y={PY} width={164} height={PH} rx={10} className="box check" />
        <text x={mid} y={PY + 18} className="n-name">Physics check</text>
        <text x={mid} y={PY + 34} className="n-sub">{tested != null ? `${tested} option${tested === 1 ? "" : "s"} tested · ${safe} safe` : physState === "busy" ? "testing every option" : physState === "done" ? "no options to test" : "waiting"}</text>
      </g>

      {!running && !failed && plans.length === 0 && <text x={mid} y={QY + 18} className="n-sub">no switching needed</text>}
      {plans.map((p, k) => (
        <g key={p.id} className={`node done plan ${p.safe ? "safe" : "unsafe"} ${selectedPlan === p.id ? "focus" : ""}`} role="button" tabIndex={0}
          onClick={() => onPlan(p.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPlan(p.id); } }}>
          <title>{`Plan ${p.id.replace(/^P/, "")}: ${p.safe ? "passes" : "fails"} the physics check${p.from_feedback ? "; shaped by your feedback" : ""}. Click to see it on the map.`}</title>
          <rect x={qx(k)} y={QY} width={qw} height={QH} rx={8} className="box" />
          <text x={qx(k) + qw / 2} y={QY + (p.from_feedback ? 13 : 19)} className="n-name">Plan {p.id.replace(/^P/, "")} {p.safe ? "✓" : "✕"}</text>
          {p.from_feedback && <text x={qx(k) + qw / 2} y={QY + 24} className="n-sub">from your feedback</text>}
        </g>
      ))}
    </svg>
  );
}
