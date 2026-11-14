import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
import { Line as DreiLine, OrbitControls } from "@react-three/drei";
import { useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import * as THREE from "three";
import type { Action, Bus, GridView, Line, LineSim, Report, Terrain } from "../api";

import { hazardsOf } from "../hazards";
import { Clouds, HazardLayer, HeatSun, Rain, WindStreaks, ZoneBadges } from "./Effects";
import { BLOW_VIS_M, H, Label, PortalCtx, SAG_VIS, TOWER, groundY, makeSampler, sx, sz, type Sampler } from "./sceneUtils";

export function lineColor(sim: LineSim | undefined): string {
  if (!sim || sim.status === "open") return "#6b7280";
  if (sim.violations.length || sim.loading_pct > 100) return "#ef4444";
  if (sim.loading_pct >= 70) return "#eab308";
  return "#22c55e";
}

// ---------------------------------------------------------------- terrain + sea
function TerrainMesh({ terrain }: { terrain: Terrain }) {
  const geom = useMemo(() => {
    const n = terrain.n, step = terrain.size_m / (n - 1);
    const pos: number[] = [], col: number[] = [], idx: number[] = [];
    const c = new THREE.Color();
    const sand = new THREE.Color("#c8b27e"), tan = new THREE.Color("#a8976a"), green = new THREE.Color("#3f7a3a"), rock = new THREE.Color("#6b7a5c");
    for (let j = 0; j < n; j++)
      for (let i = 0; i < n; i++) {
        const h = terrain.heights[j][i];
        pos.push(sx(i * step), Math.max(h, -8) * H, sz(j * step));
        if (h < 8) c.copy(sand);
        else if (h < 200) c.copy(sand).lerp(tan, Math.min(1, (h - 8) / 100));
        else if (h < 700) c.copy(tan).lerp(green, (h - 200) / 500);
        else c.copy(green).lerp(rock, Math.min(1, (h - 700) / 600));
        col.push(c.r, c.g, c.b);
      }
    for (let j = 0; j < n - 1; j++)
      for (let i = 0; i < n - 1; i++) {
        const a = j * n + i, b = a + 1, d = a + n, e = d + 1;
        idx.push(a, d, b, b, d, e);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }, [terrain]);
  return (
    <mesh geometry={geom} receiveShadow>
      <meshStandardMaterial vertexColors side={THREE.DoubleSide} roughness={0.95} />
    </mesh>
  );
}

function Sea({ flood }: { flood: number }) {
  const ref = useRef<THREE.Mesh>(null);
  const target = flood > 0 ? flood * 0.07 : -0.02; // flood level exaggerated so the surge is visible
  useFrame(({ clock }) => {
    if (ref.current) ref.current.position.y = THREE.MathUtils.lerp(ref.current.position.y, target + Math.sin(clock.elapsedTime * 1.5) * 0.01, 0.05);
  });
  return (
    <mesh ref={ref} rotation={[-Math.PI / 2, 0, 0]} position={[50, -0.02, 0]}>
      <planeGeometry args={[50, 100]} />
      <meshStandardMaterial color="#1d5b8f" transparent opacity={0.82} roughness={0.3} />
    </mesh>
  );
}

// ---------------------------------------------------------------- forest decoration
function Forest({ sampler }: { sampler: Sampler }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const pts = useMemo(() => {
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const out: [number, number, number, number][] = [];
    for (let k = 0; k < 900 && out.length < 260; k++) {
      const x = rnd() * 7500, y = rnd() * 10000;
      const h = sampler(x, y);
      const p = x < 3200 ? 0.9 : x < 6500 ? 0.25 : 0.05;
      if (h > 20 && rnd() < p) out.push([sx(x), h * H, sz(y), 0.6 + rnd() * 0.5]);
    }
    return out;
  }, [sampler]);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const m = new THREE.Matrix4();
    pts.forEach(([x, y, z, s], i) => {
      m.compose(new THREE.Vector3(x, y + 0.35 * s, z), new THREE.Quaternion(), new THREE.Vector3(s, s, s));
      ref.current!.setMatrixAt(i, m);
    });
    ref.current.instanceMatrix.needsUpdate = true;
  }, [pts]);
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, pts.length]}>
      <coneGeometry args={[0.25, 0.7, 6]} />
      <meshStandardMaterial color="#1f4d2b" />
    </instancedMesh>
  );
}

// ---------------------------------------------------------------- power lines
type Preview = { open: Set<string>; close: Set<string>; buses: Set<string> };

function linePoints(line: Line, buses: Record<string, Bus>, sampler: Sampler, sag: number, blowout: number): THREE.Vector3[] {
  const a = buses[line.from_bus], b = buses[line.to_bus];
  const spans = Math.max(2, Math.round(line.length_m / 650));
  const dip = sag * SAG_VIS;
  const L = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const nx = (b.y - a.y) / L, ny = -(b.x - a.x) / L; // right-hand normal: positive blowout swings this way
  const bow = blowout * BLOW_VIS_M;
  const pts: THREE.Vector3[] = [];
  const per = 8;
  for (let s = 0; s < spans; s++)
    for (let k = 0; k <= per; k++) {
      if (s > 0 && k === 0) continue;
      const f = k / per, t = (s + f) / spans;
      const lat = bow * 4 * f * (1 - f);
      const x = a.x + (b.x - a.x) * t + nx * lat, y = a.y + (b.y - a.y) * t + ny * lat;
      pts.push(new THREE.Vector3(sx(x), groundY(sampler, x, y) + TOWER - dip * 4 * f * (1 - f), sz(y)));
    }
  return pts;
}

const curve0 = (pts: THREE.Vector3[]) => new THREE.CatmullRomCurve3(pts);

function towerPoints(line: Line, buses: Record<string, Bus>, sampler: Sampler) {
  const a = buses[line.from_bus], b = buses[line.to_bus];
  const spans = Math.max(2, Math.round(line.length_m / 650));
  const out: [number, number, number][] = [];
  for (let s = 1; s < spans; s++) {
    const t = s / spans, x = a.x + (b.x - a.x) * t, y = a.y + (b.y - a.y) * t;
    out.push([sx(x), groundY(sampler, x, y), sz(y)]);
  }
  return out;
}

function PowerLine(props: {
  line: Line; sim: LineSim; buses: Record<string, Bus>; sampler: Sampler; preview: Preview;
  onToggle: (id: string) => void; onHover: (id: string | null, p?: THREE.Vector3) => void;
}) {
  const { line, sim, buses, sampler, preview } = props;
  const matRef = useRef<THREE.MeshStandardMaterial>(null);
  const blow = sim.energized ? sim.blowout_m : 0;
  const pts = useMemo(() => linePoints(line, buses, sampler, sim.sag_m, blow), [line, buses, sampler, sim.sag_m, blow]);
  const hot = sim.energized && sim.temp_c >= 55;
  const glow = useMemo(() => (hot ? new THREE.TubeGeometry(curve0(pts), pts.length * 2, 0.26, 8, false) : null), [hot, pts]);
  const glowRef = useRef<THREE.MeshBasicMaterial>(null);
  const curve = useMemo(() => new THREE.CatmullRomCurve3(pts), [pts]);
  const tube = useMemo(() => new THREE.TubeGeometry(curve, pts.length * 2, 0.09, 6, false), [curve, pts.length]);
  const hit = useMemo(() => new THREE.TubeGeometry(curve, pts.length, 0.75, 5, false), [curve, pts.length]);
  const towers = useMemo(() => towerPoints(line, buses, sampler), [line, buses, sampler]);
  const willOpen = preview.open.has(line.id), willClose = preview.close.has(line.id);
  let color = lineColor(sim);
  if (willClose) color = "#22d3ee";
  const violating = sim.energized && (sim.violations.length > 0 || sim.loading_pct > 100);
  useFrame(({ clock }) => {
    if (!matRef.current) return;
    const pulse = violating ? 0.6 + 0.6 * Math.sin(clock.elapsedTime * 6) : willOpen || willClose ? 0.8 : 0.25;
    matRef.current.emissiveIntensity = pulse;
    if (glowRef.current) glowRef.current.opacity = 0.25 + 0.18 * Math.sin(clock.elapsedTime * 2.5);
  });
  return (
    <group>
      <mesh geometry={tube}>
        <meshStandardMaterial ref={matRef} color={color} emissive={color} emissiveIntensity={0.3} transparent opacity={willOpen ? 0.35 : 1} />
      </mesh>
      {glow && (
        <mesh geometry={glow}>
          <meshBasicMaterial ref={glowRef} color={sim.temp_c > line.max_temp_c ? "#ff3b1f" : "#ff9a3d"} transparent opacity={0.3} depthWrite={false} />
        </mesh>
      )}
      {willOpen && <DreiLine points={pts} color="#ffffff" lineWidth={3} dashed dashSize={0.6} gapSize={0.4} />}
      <mesh
        geometry={hit}
        onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); props.onToggle(line.id); }}
        onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); document.body.style.cursor = "pointer"; props.onHover(line.id, e.point.clone()); }}
        onPointerMove={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); props.onHover(line.id, e.point.clone()); }}
        onPointerOut={() => { document.body.style.cursor = "default"; props.onHover(null); }}
      >
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      {towers.map((p, i) => (
        <mesh key={i} position={[p[0], p[1] + TOWER / 2, p[2]]}>
          <cylinderGeometry args={[0.04, 0.08, TOWER, 4]} />
          <meshStandardMaterial color="#9ca3af" />
        </mesh>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------- substations, trees, reports
function Substation({ bus, sampler, flooded, dark, backup, preview }: { bus: Bus; sampler: Sampler; flooded: boolean; dark: boolean; backup: boolean; preview: boolean }) {
  const ref = useRef<THREE.MeshStandardMaterial>(null);
  useFrame(({ clock }) => {
    if (ref.current && flooded) ref.current.emissiveIntensity = 0.5 + 0.5 * Math.sin(clock.elapsedTime * 4);
  });
  const y = groundY(sampler, bus.x, bus.y);
  const base = bus.critical_type === "hospital" ? "#f8fafc" : bus.critical_type === "water" ? "#93c5fd" : "#cbd5e1";
  const color = flooded ? "#2563eb" : dark ? "#374151" : base;
  const label =
    bus.critical_type === "hospital" ? <span className="lbl hospital">✚ {bus.name}{backup ? " (backup)" : ""}</span>
    : bus.critical_type === "water" ? <span className="lbl water">💧 {bus.name}{backup ? " (backup)" : ""}</span>
    : <span className="lbl">{bus.id} {bus.name}</span>;
  return (
    <group position={[sx(bus.x), y, sz(bus.y)]}>
      <mesh position={[0, 0.45, 0]}>
        <boxGeometry args={[1.3, 0.9, 1.3]} />
        <meshStandardMaterial ref={ref} color={color} emissive={flooded ? "#3b82f6" : preview ? "#a855f7" : "#000"} emissiveIntensity={preview ? 0.9 : 0.4} />
      </mesh>
      {preview && (
        <mesh position={[0, 0.45, 0]}>
          <boxGeometry args={[1.6, 1.2, 1.6]} />
          <meshBasicMaterial color="#a855f7" wireframe />
        </mesh>
      )}
      <Label position={[0, 1.6, 0]} center distanceFactor={60} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        {label}
      </Label>
    </group>
  );
}

function LineTrees({ trees, lines, buses, sampler }: { trees: GridView["trees"]; lines: Record<string, Line>; buses: Record<string, Bus>; sampler: Sampler }) {
  const items = useMemo(
    () =>
      trees.map((t) => {
        const ln = lines[t.line], a = buses[ln.from_bus], b = buses[ln.to_bus];
        const dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy);
        const nx = dy / L, ny = -dx / L;
        const bx = t.x - nx * t.lateral_offset_m, by = t.y - ny * t.lateral_offset_m;
        // exaggerate lateral offset so trees read next to (not on) the wire
        const off = Math.sign(t.lateral_offset_m) * (25 + Math.abs(t.lateral_offset_m) * 12);
        const x = bx + nx * off, y = by + ny * off;
        const s = t.height_m / 12;
        return { x: sx(x), y: groundY(sampler, x, y), z: sz(y), s, close: Math.abs(t.lateral_offset_m) < 5 };
      }),
    [trees, lines, buses, sampler]
  );
  return (
    <>
      {items.map((it, i) => (
        <mesh key={i} position={[it.x, it.y + 0.5 * it.s, it.z]} scale={[it.s, it.s, it.s]}>
          <coneGeometry args={[0.32, 1.0, 7]} />
          <meshStandardMaterial color={it.close ? "#166534" : "#2f6b3a"} />
        </mesh>
      ))}
    </>
  );
}

const KIND_ICON: Record<string, string> = {
  wire_down: "⚡", sparking: "✨", smoke: "💨", tree_on_line: "🌲", water_at_substation: "🌊", outage: "🔌",
};

function ReportPin({ r, sampler }: { r: Report; sampler: Sampler }) {
  const y = groundY(sampler, r.x, r.y);
  return (
    <group position={[sx(r.x), y, sz(r.y)]}>
      <mesh position={[0, 1.1, 0]}>
        <cylinderGeometry args={[0.04, 0.04, 2.2, 6]} />
        <meshStandardMaterial color="#e5e7eb" />
      </mesh>
      <mesh position={[0, 2.3, 0]}>
        <sphereGeometry args={[0.3, 12, 12]} />
        <meshStandardMaterial color={r.corroborated ? "#f97316" : "#a78bfa"} emissive={r.corroborated ? "#f97316" : "#7c3aed"} emissiveIntensity={0.6} />
      </mesh>
      {r.corroborated && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.1, 0]}>
          <torusGeometry args={[0.8, 0.09, 8, 32]} />
          <meshBasicMaterial color="#f97316" />
        </mesh>
      )}
      <Label position={[0, 3.0, 0]} center distanceFactor={60} style={{ pointerEvents: "none" }}>
        <span className={"pin" + (r.corroborated ? " corr" : "")}>{KIND_ICON[r.kind] ?? "❗"} {r.id}</span>
      </Label>
    </group>
  );
}

// ---------------------------------------------------------------- tooltip
function Tooltip({ line, sim, at }: { line: Line; sim: LineSim; at: THREE.Vector3 }) {
  return (
    <Label position={[at.x, at.y + 1.2, at.z]} style={{ pointerEvents: "none" }} zIndexRange={[100, 50]}>
      <div className="tooltip">
        <b>{line.id}</b> <span className="muted">{line.zone} · {line.from_bus}→{line.to_bus} · {sim.status}</span>
        <div>Flow {sim.flow_mw.toFixed(1)} MW · {sim.amps.toFixed(0)} A ({sim.loading_pct.toFixed(0)}%)</div>
        <div>Temp {sim.temp_c.toFixed(1)} °C · Sag {sim.sag_m.toFixed(2)} m</div>
        <div>Tree clearance {sim.clearance_m.toFixed(2)} m · Fire risk {sim.fire_risk.toFixed(2)}</div>
        {sim.violations.map((v) => <div key={v} className="bad">⚠ {v}</div>)}
        <div className="muted">click to {sim.status === "open" ? "close" : "open"} breaker</div>
      </div>
    </Label>
  );
}

// ---------------------------------------------------------------- main
export default function Scene({ grid, terrain, previewActions, onToggle, overlay }: {
  grid: GridView; terrain: Terrain; previewActions: Action[] | null; onToggle: (id: string) => void;
  overlay: RefObject<HTMLDivElement | null>;
}) {
  const sampler = useMemo(() => makeSampler(terrain), [terrain]);
  const buses = useMemo(() => Object.fromEntries(grid.buses.map((b) => [b.id, b])), [grid.buses]);
  const lines = useMemo(() => Object.fromEntries(grid.lines.map((l) => [l.id, l])), [grid.lines]);
  const [hover, setHover] = useState<{ id: string; p: THREE.Vector3 } | null>(null);
  const preview: Preview = useMemo(() => {
    const p: Preview = { open: new Set(), close: new Set(), buses: new Set() };
    for (const a of previewActions ?? []) {
      if (a.type === "open_line") p.open.add(a.target);
      else if (a.type === "close_line") p.close.add(a.target);
      else if (a.type === "deenergize_bus" || a.type === "shed_load") p.buses.add(a.target);
    }
    return p;
  }, [previewActions]);
  const flood = grid.weather.coast?.flood_level_m ?? 0;
  const hazards = useMemo(() => hazardsOf(grid), [grid]);
  const atm = ATMOSPHERE[grid.scenario] ?? ATMOSPHERE.Normal;
  const w = grid.weather;
  const rainy = Object.values(w).some((z) => z.humidity_pct >= 90);
  const stormy = Object.values(w).some((z) => z.gust_ms >= 14);
  const hotDay = Object.values(w).some((z) => z.air_temp_c >= 33);
  const windX = Math.sin(((w.coast?.wind_dir_deg ?? 90) * Math.PI) / 180) * (w.coast?.wind_speed_ms ?? 0);

  return (
    <Canvas camera={{ position: [-2, 74, 104], fov: 44 }} onPointerMissed={() => setHover(null)}>
      <PortalCtx.Provider value={overlay}>
      <color attach="background" args={[atm.sky]} />
      <fog attach="fog" args={[atm.sky, atm.fogNear, 260]} />
      <ambientLight intensity={atm.ambient} />
      <directionalLight position={[-40, 80, 30]} intensity={atm.sun} color={atm.sunColor} />
      <hemisphereLight args={["#bcd7ff", "#2a3a1f", 0.35]} />
      {hotDay && <HeatSun />}
      {(stormy || rainy) && <Clouds color={rainy ? "#4b5563" : "#6b7280"} count={rainy ? 22 : 14} speed={stormy ? 6 : 2} />}
      {rainy && <Rain windX={windX} />}
      <WindStreaks weather={w} sampler={sampler} />
      <HazardLayer hazards={hazards} grid={grid} lines={lines} buses={buses} sampler={sampler} />
      <TerrainMesh terrain={terrain} />
      <Sea flood={flood} />
      <Forest sampler={sampler} />
      <LineTrees trees={grid.trees} lines={lines} buses={buses} sampler={sampler} />
      {grid.lines.map((l) => (
        <PowerLine
          key={l.id}
          line={l}
          sim={grid.sim.lines[l.id]}
          buses={buses}
          sampler={sampler}
          preview={preview}
          onToggle={onToggle}
          onHover={(id, p) => setHover(id && p ? { id, p } : null)}
        />
      ))}
      {grid.buses.map((b) => {
        const s = grid.sim.buses[b.id];
        return (
          <Substation key={b.id} bus={b} sampler={sampler} flooded={s.flooded} dark={!s.energized || s.unserved_mw > 0.05}
            backup={s.on_backup} preview={preview.buses.has(b.id)} />
        );
      })}
      {grid.reports.map((r) => <ReportPin key={r.id} r={r} sampler={sampler} />)}
      {hover && lines[hover.id] && <Tooltip line={lines[hover.id]} sim={grid.sim.lines[hover.id]} at={hover.p} />}
      <ZoneBadges weather={w} sampler={sampler} />
      <OrbitControls target={[4, 0, -8]} maxPolarAngle={Math.PI / 2.15} minDistance={15} maxDistance={180} />
      </PortalCtx.Provider>
    </Canvas>
  );
}

const ATMOSPHERE: Record<string, { sky: string; fogNear: number; ambient: number; sun: number; sunColor: string }> = {
  Normal: { sky: "#0b1020", fogNear: 120, ambient: 0.55, sun: 1.3, sunColor: "#ffffff" },
  "Heat Wave": { sky: "#2b1408", fogNear: 90, ambient: 0.65, sun: 1.9, sunColor: "#ffc48a" },
  "Wind Storm": { sky: "#151a24", fogNear: 100, ambient: 0.45, sun: 0.95, sunColor: "#d6e2ff" },
  "Coastal Flood": { sky: "#0d1622", fogNear: 70, ambient: 0.4, sun: 0.75, sunColor: "#b8c7dd" },
};
