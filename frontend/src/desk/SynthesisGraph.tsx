import type { Synthesis } from "../api";

/** How the team's work came together, drawn from what the backend recorded (backend/agents/synthesis.py):
 *  each agent reports to the coordinator, the coordinator builds options, the physics check tests them, plans come out.
 *  A thicker line means the agent flagged more; a blue line means a plan step came from that agent. */
export default function SynthesisGraph({ g }: { g: Synthesis }) {
  const W = 372, pitch = 30, top = 6;
  const H = Math.max(top * 2 + g.agents.length * pitch, 112);
  const mid = H / 2;
  const ax = 2, aw = 98, cx = 122, cw = 78, px = 216, pw = 76, qx = 308, qw = 62;
  const ay = (k: number) => top + k * pitch;
  const py = (k: number) => mid - (g.plans.length * 26) / 2 + k * 26;
  const most = Math.max(1, ...g.agents.map((a) => a.findings));
  return (
    <svg className="synth" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="How the agents' findings became plans">
      {g.agents.map((a, k) => {
        const y = ay(k) + 12;
        return <path key={a.id} d={`M${ax + aw},${y} C${ax + aw + 16},${y} ${cx - 16},${mid} ${cx},${mid}`} fill="none"
          stroke={a.raised_steps ? "#007aff" : "#c7c7cc"} strokeWidth={1 + (3 * a.findings) / most} opacity={a.findings ? 0.85 : 0.4} />;
      })}
      <line x1={cx + cw} y1={mid} x2={px} y2={mid} stroke="#8e8e93" strokeWidth={2} />
      {g.plans.map((p, k) => <path key={p.id} d={`M${px + pw},${mid} C${px + pw + 10},${mid} ${qx - 10},${py(k) + 11} ${qx},${py(k) + 11}`} fill="none" stroke={p.safe ? "#34a36b" : "#ff3b30"} strokeWidth={2} />)}

      {g.agents.map((a, k) => (
        <g key={a.id}>
          <rect x={ax} y={ay(k)} width={aw} height={24} rx={7} fill="#f5f5f7" stroke={a.act_now ? "#ff3b30" : "#d1d1d6"} />
          <text x={ax + 7} y={ay(k) + 10.5} fontSize="9.5" fontWeight={600} fill="#1d1d1f">{a.name} agent</text>
          <text x={ax + 7} y={ay(k) + 20} fontSize="8" fill="#6e6e73">{a.findings ? `${a.findings} flagged${a.act_now ? ` · ${a.act_now} urgent` : ""}` : "nothing to flag"}</text>
        </g>
      ))}

      <rect x={cx} y={mid - 26} width={cw} height={52} rx={9} fill="#1d1d1f" />
      <text x={cx + cw / 2} y={mid - 10} fontSize="9.5" fontWeight={600} fill="#fff" textAnchor="middle">Coordinator</text>
      <text x={cx + cw / 2} y={mid + 2} fontSize="8" fill="#d1d1d6" textAnchor="middle">{g.coordinator.candidates != null ? `${g.coordinator.candidates} moves weighed` : "weighs the findings"}</text>
      <text x={cx + cw / 2} y={mid + 13} fontSize="8" fill="#d1d1d6" textAnchor="middle">
        {g.coordinator.feedback ? "used your feedback" : g.coordinator.disagreements ? `${g.coordinator.disagreements} dispute settled` : "no disputes"}
      </text>

      <rect x={px} y={mid - 26} width={pw} height={52} rx={9} fill="#fff" stroke="#007aff" strokeWidth={1.5} />
      <text x={px + pw / 2} y={mid - 10} fontSize="9.5" fontWeight={600} fill="#0a5bc4" textAnchor="middle">Physics check</text>
      <text x={px + pw / 2} y={mid + 2} fontSize="8" fill="#6e6e73" textAnchor="middle">{g.physics.screened != null ? `${g.physics.screened} tested` : `${g.physics.verified} re-run`}</text>
      <text x={px + pw / 2} y={mid + 13} fontSize="8" fill="#6e6e73" textAnchor="middle">{g.physics.safe != null ? `${g.physics.safe} safe` : "every plan"}</text>

      {g.plans.length === 0 && <text x={qx + qw / 2} y={mid + 3} fontSize="8.5" fill="#6e6e73" textAnchor="middle">no plan needed</text>}
      {g.plans.map((p, k) => (
        <g key={p.id}>
          <rect x={qx} y={py(k)} width={qw} height={22} rx={7} fill={p.safe ? "rgba(52,163,107,0.14)" : "rgba(255,59,48,0.1)"} stroke={p.safe ? "#34a36b" : "#ff3b30"} />
          <text x={qx + qw / 2} y={py(k) + (p.from_feedback ? 9.5 : 14)} fontSize="9.5" fontWeight={600} fill={p.safe ? "#1d6b45" : "#c21d12"} textAnchor="middle">Plan {p.id.replace(/^P/, "")} {p.safe ? "✓" : "✕"}</text>
          {p.from_feedback && <text x={qx + qw / 2} y={py(k) + 18.5} fontSize="7.5" fill="#6e6e73" textAnchor="middle">from your feedback</text>}
        </g>
      ))}
    </svg>
  );
}
