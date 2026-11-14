/** Visual disaster layer: wind, rain, clouds, sun, fire, sparks, overloads, flood ripples, on-map callouts.
 *  Purely cosmetic. It reads weather + physics results; it never computes physics itself. */
import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import type { Bus, GridView, Line, Weather } from "../api";
import { HAZARD_STYLE, type Hazard } from "../hazards";
import { Label, TOWER, groundY, lineMid, sx, sz, type Sampler } from "./sceneUtils";

const ZONE_X: Record<string, [number, number]> = { ridge: [0, 3200], valley: [3200, 7200], coast: [7200, 9300] };

function rng(seed: number) {
  let s = seed;
  return () => (s = (s * 16807) % 2147483647) / 2147483647;
}

// ---------------------------------------------------------------- wind streaks
export function WindStreaks({ weather, sampler }: { weather: Record<string, Weather>; sampler: Sampler }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const parts = useMemo(() => {
    const r = rng(11);
    const out: { zone: string; x0: number; y0: number; h: number; phase: number; speed: number; dir: number; len: number }[] = [];
    for (const [zone, w] of Object.entries(weather)) {
      if (w.gust_ms < 10) continue;
      const n = Math.round(w.gust_ms * 7);
      for (let i = 0; i < n; i++)
        out.push({
          zone, x0: r() * 10000, y0: r() * 10000, h: 0.8 + r() * 4.5, phase: r() * 10000,
          speed: w.wind_speed_ms * (50 + r() * 40), dir: w.wind_dir_deg, len: 0.6 + w.gust_ms / 14,
        });
    }
    return out;
  }, [weather]);
  const m = useMemo(() => new THREE.Matrix4(), []);
  const q = useMemo(() => new THREE.Quaternion(), []);
  const v = useMemo(() => new THREE.Vector3(), []);
  const sc = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    const t = clock.elapsedTime;
    parts.forEach((p, i) => {
      const [x0, x1] = ZONE_X[p.zone];
      const rad = (p.dir * Math.PI) / 180, dx = Math.sin(rad), dy = Math.cos(rad);
      const d = t * p.speed + p.phase;
      const x = x0 + ((((p.x0 + dx * d) - x0) % (x1 - x0)) + (x1 - x0)) % (x1 - x0);
      const y = (((p.y0 + dy * d) % 10000) + 10000) % 10000;
      v.set(sx(x), groundY(sampler, x, y) + p.h, sz(y));
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.atan2(dy, dx));
      const fade = Math.min(1, (x - x0) / 400, (x1 - x) / 400);
      sc.set(p.len * Math.max(0.05, fade), 1, 1);
      m.compose(v, q, sc);
      ref.current!.setMatrixAt(i, m);
    });
    ref.current.instanceMatrix.needsUpdate = true;
  });
  if (!parts.length) return null;
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, parts.length]} key={parts.length}>
      <boxGeometry args={[2.4, 0.09, 0.09]} />
      <meshBasicMaterial color="#f0f9ff" transparent opacity={0.75} depthWrite={false} />
    </instancedMesh>
  );
}

// ---------------------------------------------------------------- rain
export function Rain({ windX }: { windX: number }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const N = 900;
  const drops = useMemo(() => {
    const r = rng(5);
    return Array.from({ length: N }, () => ({ x: r() * 110 - 55, z: r() * 110 - 55, phase: r() * 30, speed: 22 + r() * 8 }));
  }, []);
  const m = useMemo(() => new THREE.Matrix4(), []);
  const q = useMemo(() => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), -windX * 0.03), [windX]);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    const t = clock.elapsedTime;
    drops.forEach((d, i) => {
      const fall = (t * d.speed + d.phase) % 30;
      m.compose(new THREE.Vector3(d.x + fall * windX * 0.03, 30 - fall, d.z), q, new THREE.Vector3(1, 1, 1));
      ref.current!.setMatrixAt(i, m);
    });
    ref.current.instanceMatrix.needsUpdate = true;
  });
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, N]}>
      <boxGeometry args={[0.06, 1.0, 0.06]} />
      <meshBasicMaterial color="#b5d3ff" transparent opacity={0.55} depthWrite={false} />
    </instancedMesh>
  );
}

// ---------------------------------------------------------------- clouds + sun
export function Clouds({ color, count, speed }: { color: string; count: number; speed: number }) {
  const group = useRef<THREE.Group>(null);
  const clouds = useMemo(() => {
    const r = rng(3);
    return Array.from({ length: count }, () => ({
      x: r() * 170 - 85, z: -60 - r() * 45, y: 14 + r() * 14, s: 4 + r() * 5,
      puffs: Array.from({ length: 4 }, () => [r() * 6 - 3, r() * 1.2, r() * 4 - 2, 0.7 + r() * 0.6] as const),
    }));
  }, [count]);
  useFrame((_, dt) => {
    group.current?.children.forEach((c) => {
      c.position.x += dt * speed;
      if (c.position.x > 90) c.position.x = -90;
    });
  });
  return (
    <group ref={group}>
      {clouds.map((c, i) => (
        <group key={i} position={[c.x, c.y, c.z]} scale={[c.s, c.s * 0.35, c.s]}>
          {c.puffs.map(([px, py, pz, ps], j) => (
            <mesh key={j} position={[px / 3, py, pz / 3]} scale={ps}>
              <sphereGeometry args={[1, 10, 8]} />
              <meshStandardMaterial color={color} transparent opacity={0.82} depthWrite={false} />
            </mesh>
          ))}
        </group>
      ))}
    </group>
  );
}

export function HeatSun() {
  const halo = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (halo.current) halo.current.scale.setScalar(1 + 0.06 * Math.sin(clock.elapsedTime * 1.5));
  });
  return (
    <group position={[48, 26, -110]}>
      <mesh>
        <sphereGeometry args={[5, 24, 24]} />
        <meshBasicMaterial color="#ffe08a" fog={false} />
      </mesh>
      <mesh ref={halo}>
        <sphereGeometry args={[9, 24, 24]} />
        <meshBasicMaterial color="#ff8a3d" transparent opacity={0.3} depthWrite={false} fog={false} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------- hazard effects at a point
function Flames({ big }: { big: boolean }) {
  const refs = [useRef<THREE.Mesh>(null), useRef<THREE.Mesh>(null), useRef<THREE.Mesh>(null)];
  const light = useRef<THREE.PointLight>(null);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    refs.forEach((r, i) => {
      if (!r.current) return;
      const f = 0.75 + 0.35 * Math.sin(t * (9 + i * 3) + i) + 0.15 * Math.sin(t * 23 + i * 2);
      r.current.scale.set(1, f, 1);
    });
    if (light.current) light.current.intensity = 18 + 10 * Math.sin(t * 13);
  });
  const s = big ? 2.2 : 1.4;
  return (
    <group scale={s}>
      <mesh ref={refs[0]} position={[0, 0.7, 0]}>
        <coneGeometry args={[0.55, 1.6, 10]} />
        <meshBasicMaterial color="#f97316" transparent opacity={0.9} />
      </mesh>
      <mesh ref={refs[1]} position={[0.3, 0.5, 0.15]}>
        <coneGeometry args={[0.35, 1.1, 10]} />
        <meshBasicMaterial color="#facc15" transparent opacity={0.9} />
      </mesh>
      <mesh ref={refs[2]} position={[-0.28, 0.45, -0.1]}>
        <coneGeometry args={[0.3, 0.9, 10]} />
        <meshBasicMaterial color="#ef4444" transparent opacity={0.9} />
      </mesh>
      <pointLight ref={light} color="#ff7a1a" distance={12} intensity={20} position={[0, 1.2, 0]} />
    </group>
  );
}

function Smoke() {
  const puffs = useMemo(() => Array.from({ length: 7 }, (_, i) => i / 7), []);
  const refs = useRef<(THREE.Mesh | null)[]>([]);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime * 0.35;
    puffs.forEach((p, i) => {
      const m = refs.current[i];
      if (!m) return;
      const k = (t + p) % 1;
      m.position.set(k * 4, 2 + k * 11, Math.sin(i * 2.1) * 0.8 * k);
      m.scale.setScalar(0.6 + k * 3);
      (m.material as THREE.MeshBasicMaterial).opacity = 0.55 * (1 - k);
    });
  });
  return (
    <group>
      {puffs.map((_, i) => (
        <mesh key={i} ref={(el) => { refs.current[i] = el; }}>
          <sphereGeometry args={[1, 8, 8]} />
          <meshBasicMaterial color="#4b5563" transparent opacity={0.4} depthWrite={false} />
        </mesh>
      ))}
    </group>
  );
}

function Sparks({ color = "#fde047" }: { color?: string }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const N = 18;
  const sp = useMemo(() => {
    const r = rng(17);
    return Array.from({ length: N }, () => ({ vx: r() * 2 - 1, vz: r() * 2 - 1, vy: 1.5 + r() * 2.5, ph: r() }));
  }, []);
  const m = useMemo(() => new THREE.Matrix4(), []);
  useFrame(({ clock }) => {
    if (!ref.current) return;
    sp.forEach((s, i) => {
      const k = (clock.elapsedTime * 1.4 + s.ph) % 1;
      const x = s.vx * k * 1.6, z = s.vz * k * 1.6, y = s.vy * k - 4.5 * k * k;
      const sc = 1 - k;
      m.compose(new THREE.Vector3(x, y, z), new THREE.Quaternion(), new THREE.Vector3(sc, sc, sc));
      ref.current!.setMatrixAt(i, m);
    });
    ref.current.instanceMatrix.needsUpdate = true;
  });
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, N]}>
      <sphereGeometry args={[0.09, 6, 6]} />
      <meshBasicMaterial color={color} />
    </instancedMesh>
  );
}

function PulseRing({ color, radius, speed = 1.2, y = 0.1 }: { color: string; radius: number; speed?: number; y?: number }) {
  const refs = [useRef<THREE.Mesh>(null), useRef<THREE.Mesh>(null)];
  useFrame(({ clock }) => {
    refs.forEach((r, i) => {
      if (!r.current) return;
      const k = (clock.elapsedTime * speed * 0.5 + i * 0.5) % 1;
      r.current.scale.setScalar(0.3 + k * 1.2);
      (r.current.material as THREE.MeshBasicMaterial).opacity = 0.9 * (1 - k);
    });
  });
  return (
    <>
      {refs.map((r, i) => (
        <mesh key={i} ref={r} rotation={[-Math.PI / 2, 0, 0]} position={[0, y, 0]}>
          <torusGeometry args={[radius, 0.12, 8, 40]} />
          <meshBasicMaterial color={color} transparent opacity={0.8} depthWrite={false} />
        </mesh>
      ))}
    </>
  );
}

// ---------------------------------------------------------------- hazards on the map
type Spot = { pos: [number, number, number]; hazard: Hazard };

function spotsOf(hazards: Hazard[], grid: GridView, lines: Record<string, Line>, buses: Record<string, Bus>, sampler: Sampler): Spot[] {
  const spots: Spot[] = [];
  for (const h of hazards) {
    if (lines[h.target]) {
      const mid = lineMid(lines[h.target], buses);
      const s = grid.sim.lines[h.target];
      // fire / contact sits between the bowed wire and the trees it swings toward
      const side = Math.sign(s.blowout_m) || 1;
      const off = h.kind === "fire" || h.kind === "contact" ? 70 * side : 0;
      const x = mid.x + mid.nx * off, y = mid.y + mid.ny * off;
      spots.push({ pos: [sx(x), groundY(sampler, x, y), sz(y)], hazard: h });
    } else if (buses[h.target]) {
      const b = buses[h.target];
      spots.push({ pos: [sx(b.x), groundY(sampler, b.x, b.y), sz(b.y)], hazard: h });
    }
  }
  return spots;
}

export function HazardLayer({ hazards, grid, lines, buses, sampler }: {
  hazards: Hazard[]; grid: GridView; lines: Record<string, Line>; buses: Record<string, Bus>; sampler: Sampler;
}) {
  const spots = useMemo(() => spotsOf(hazards, grid, lines, buses, sampler), [hazards, grid, lines, buses, sampler]);
  const floodY = (grid.weather.coast?.flood_level_m ?? 0) * 0.07 + 0.05;
  return (
    <>
      {spots.map(({ pos, hazard: h }) => {
        const st = HAZARD_STYLE[h.kind];
        const isBus = !!buses[h.target];
        return (
          <group key={h.target} position={pos}>
            {h.kind === "fire" && (
              <>
                <Flames big={grid.sim.lines[h.target].fire_risk >= 0.7} />
                {grid.sim.lines[h.target].fire_risk >= 0.5 && <Smoke />}
                <group position={[0, TOWER * 0.8, 0]} scale={1.8}><Sparks /></group>
              </>
            )}
            {h.kind === "contact" && (
              <>
                <group position={[0, TOWER * 0.8, 0]} scale={1.8}><Sparks color="#fef08a" /></group>
                <PulseRing color={st.color} radius={1.2} />
              </>
            )}
            {h.kind === "heat" && <PulseRing color={st.color} radius={1.6} speed={0.8} y={TOWER} />}
            {h.kind === "overload" && <PulseRing color={st.color} radius={1.8} speed={2} y={TOWER} />}
            {h.kind === "flood" && <group position={[0, floodY - pos[1], 0]}><PulseRing color={st.color} radius={2.2} speed={0.7} /></group>}
            {(h.kind === "outage" || h.kind === "backup") && <PulseRing color={st.color} radius={1.4} speed={0.6} />}
            <Label position={[0, isBus ? 4.6 : h.kind === "fire" ? 5.2 : 2.4, 0]} center distanceFactor={55} zIndexRange={[40, 20]} style={{ pointerEvents: "none" }}>
              <div className="callout" style={{ borderColor: st.color }}>
                <span>{h.icon}</span> {h.short}
              </div>
            </Label>
          </group>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------- zone weather badges
export function ZoneBadges({ weather, sampler }: { weather: Record<string, Weather>; sampler: Sampler }) {
  const z = [
    { k: "ridge", t: "RIDGE", x: 1700, y: 900 },
    { k: "valley", t: "VALLEY", x: 5200, y: 900 },
    { k: "coast", t: "COAST", x: 8200, y: 900 },
  ];
  return (
    <>
      {z.map((l) => {
        const w = weather[l.k];
        const hot = w.air_temp_c >= 33, windy = w.gust_ms >= 14, dry = w.humidity_pct < 20, wet = w.flood_level_m > 0;
        return (
          <Label key={l.t} position={[sx(l.x), groundY(sampler, l.x, l.y) + 3, sz(l.y)]} center distanceFactor={80} style={{ pointerEvents: "none" }}>
            <div className="zonebox">
              <div className="zone">{l.t}</div>
              <div className="zwx">
                <span className={hot ? "hot" : ""}>🌡️ {w.air_temp_c}°C</span>
                <span className={windy ? "windy" : ""}>💨 {w.gust_ms} m/s</span>
                <span className={dry ? "dry" : ""}>💧 {w.humidity_pct}%</span>
                {wet && <span className="wet">🌊 +{w.flood_level_m} m</span>}
              </div>
            </div>
          </Label>
        );
      })}
    </>
  );
}
