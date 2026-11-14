import type { GridView } from "./api";

export type HazardKind = "fire" | "contact" | "heat" | "overload" | "flood" | "outage" | "backup";

export type Hazard = {
  kind: HazardKind;
  icon: string;
  target: string; // line or bus id
  short: string; // map callout
  plain: string; // one plain-language sentence for the banner
  rank: number; // higher = more urgent
};

export const HAZARD_STYLE: Record<HazardKind, { icon: string; color: string; label: string }> = {
  fire: { icon: "🔥", color: "#f97316", label: "wire in trees, fire risk" },
  contact: { icon: "🌳", color: "#facc15", label: "wire sagging into trees" },
  heat: { icon: "🌡️", color: "#fb7185", label: "wire overheating" },
  overload: { icon: "⚡", color: "#ef4444", label: "line overloaded" },
  flood: { icon: "🌊", color: "#38bdf8", label: "substation flooded" },
  outage: { icon: "⛔", color: "#9ca3af", label: "no power" },
  backup: { icon: "🔋", color: "#fbbf24", label: "on backup power" },
};

/** Turns the physics result into plain-language hazards. Every number comes from the physics engine. */
export function hazardsOf(grid: GridView): Hazard[] {
  const out: Hazard[] = [];
  const busName = (id: string) => grid.buses.find((b) => b.id === id)?.name ?? id;
  for (const l of grid.lines) {
    const s = grid.sim.lines[l.id];
    if (!s?.energized) continue;
    for (const v of s.violations) {
      if (v.includes("clearance")) {
        const wind = v.includes("wind");
        const fire = s.fire_risk >= 0.5;
        if (wind || fire) {
          out.push({
            kind: "fire", icon: "🔥", target: l.id, rank: 10 + s.fire_risk,
            short: `${l.id}: wire ${s.clearance_m.toFixed(1)} m from trees${fire ? ` · fire risk ${Math.round(s.fire_risk * 100)}%` : ""}`,
            plain: wind
              ? `Wind is swinging line ${l.id} to within ${s.clearance_m.toFixed(1)} m of trees${fire ? " in bone-dry air, a wildfire ignition risk" : ""}.`
              : `Line ${l.id} is ${s.clearance_m.toFixed(1)} m from trees in fire weather.`,
          });
        } else {
          out.push({
            kind: "contact", icon: "🌳", target: l.id, rank: 8,
            short: `${l.id}: hot wire sagging, ${s.clearance_m.toFixed(1)} m from trees`,
            plain: `Heat makes line ${l.id} sag ${s.sag_m.toFixed(1)} m, bringing it to ${s.clearance_m.toFixed(1)} m from trees.`,
          });
        }
      } else if (v.includes("conductor")) {
        out.push({
          kind: "heat", icon: "🌡️", target: l.id, rank: 7,
          short: `${l.id}: ${Math.round(s.temp_c)} °C (limit ${l.max_temp_c})`,
          plain: `Line ${l.id} is running at ${Math.round(s.temp_c)} °C, above its ${l.max_temp_c} °C limit, and will be damaged.`,
        });
      } else if (v.includes("loading")) {
        out.push({
          kind: "overload", icon: "⚡", target: l.id, rank: 9,
          short: `${l.id}: ${Math.round(s.loading_pct)}% of rating`,
          plain: `Line ${l.id} is overloaded at ${Math.round(s.loading_pct)}% of its rating and could trip.`,
        });
      }
    }
  }
  for (const b of grid.buses) {
    const s = grid.sim.buses[b.id];
    if (s.flooded) {
      out.push({
        kind: "flood", icon: "🌊", target: b.id, rank: s.energized ? 11 : 3,
        short: `${b.name}: flooded${s.energized ? ", still LIVE" : ", switched off"}`,
        plain: s.energized
          ? `Floodwater is over the ${b.name} substation, and it is still energized.`
          : `${b.name} substation is flooded and safely switched off.`,
      });
    }
    if (s.on_backup) {
      out.push({
        kind: "backup", icon: "🔋", target: b.id, rank: 6,
        short: `${b.name}: on backup (${b.backup_hours} h)`,
        plain: `${b.name} has lost grid power and is running on backup for about ${b.backup_hours} hours.`,
      });
    } else if (s.unserved_mw > 0.05 && !s.flooded) {
      out.push({
        kind: "outage", icon: "⛔", target: b.id, rank: 4,
        short: `${b.name}: ${Math.round(s.unserved_mw)} MW out`,
        plain: `${b.name} has ${Math.round(s.unserved_mw)} MW of customers without power.`,
      });
    }
  }
  // one callout per target: keep the most urgent
  const best = new Map<string, Hazard>();
  for (const h of out) if (!best.has(h.target) || best.get(h.target)!.rank < h.rank) best.set(h.target, h);
  return [...best.values()].sort((a, b) => b.rank - a.rank);
}

export const SCENARIO_ICON: Record<string, string> = {
  Normal: "☀️", "Heat Wave": "🔥", "Wind Storm": "💨", "Coastal Flood": "🌊",
};
