import { Canvas, useFrame, type ThreeEvent } from "@react-three/fiber";
import { Line, OrbitControls } from "@react-three/drei";
import { useMemo, useRef, useState, type RefObject } from "react";
import * as THREE from "three";
import { Label, PortalCtx } from "../components/sceneUtils";
import type { Hour, LAStatic, LAView } from "./api";

// Projection: 1 scene unit = 1 km. North is -z. Vertical exaggeration 3x (spec).
const VEX = 3;
const KM_LAT = 110.54;

type Proj = { x: (lon: number) => number; z: (lat: number) => number; kx: number };
function makeProj(s: LAStatic): Proj {
  const kx = 111.32 * Math.cos((s.proj.lat0 * Math.PI) / 180);
  return { x: (lon) => (lon - s.proj.lon0) * kx, z: (lat) => -(lat - s.proj.lat0) * KM_LAT, kx };
}
function makeHeight(s: LAStatic) {
  const { lats, lons, elev } = s.terrain;
  const dlat = lats[1] - lats[0], dlon = lons[1] - lons[0];
  return (lat: number, lon: number) => {
    const fy = Math.min(lats.length - 1.001, Math.max(0, (lat - lats[0]) / dlat));
    const fx = Math.min(lons.length - 1.001, Math.max(0, (lon - lons[0]) / dlon));
    const j = Math.floor(fy), i = Math.floor(fx), v = fy - j, u = fx - i;
    const h = (elev[j][i] * (1 - u) + elev[j][i + 1] * u) * (1 - v) + (elev[j + 1][i] * (1 - u) + elev[j + 1][i + 1] * u) * v;
    return (Math.max(h, 0) / 1000) * VEX;
  };
}

export function riskColor(r: number, alarm: number): string {
  const k = r / alarm;
  if (k >= 1) return "#ef4444";
  if (k >= 0.7) return "#f97316";
  if (k >= 0.35) return "#eab308";
  return "#22c55e";
}
export function loadColor(p: number): string {
  if (p > 100) return "#ef4444";
  if (p >= 70) return "#eab308";
  return "#22c55e";
}

// ---------------------------------------------------------------- terrain
function Terrain({ s, proj }: { s: LAStatic; proj: Proj }) {
  const w = proj.x(s.bbox.lon_max) - proj.x(s.bbox.lon_min), d = proj.z(s.bbox.lat_min) - proj.z(s.bbox.lat_max);
  const geom = useMemo(() => {
    const { lats, lons, elev } = s.terrain;
    const ny = lats.length, nx = lons.length;
    const pos = new Float32Array(nx * ny * 3), col = new Float32Array(nx * ny * 3);
    const c = new THREE.Color();
    const stops: [number, string][] = [[-1e9, "#0f2f4f"], [0, "#c9b98f"], [60, "#b5a98a"], [250, "#a39672"], [600, "#6e7c4a"], [1200, "#55673a"], [1800, "#7d7462"], [2400, "#b9b3a6"]];
    const tmp = new THREE.Color();
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i, h = elev[j][i];
        pos[k * 3] = proj.x(lons[i]);
        pos[k * 3 + 1] = (Math.max(h, -60) / 1000) * VEX;
        pos[k * 3 + 2] = proj.z(lats[j]);
        let a = 0;
        while (a < stops.length - 1 && h >= stops[a + 1][0]) a++;
        const b = Math.min(a + 1, stops.length - 1);
        const t = b === a ? 0 : Math.max(0, Math.min(1, (h - stops[a][0]) / Math.max(1, stops[b][0] - stops[a][0])));
        c.set(stops[a][1]).lerp(tmp.set(stops[b][1]), a === 0 ? 0 : t);
        col.set([c.r, c.g, c.b], k * 3);
      }
    const idx: number[] = [];
    for (let j = 0; j < ny - 1; j++)
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i, b = a + 1, d = a + nx, e = d + 1;
        idx.push(a, b, d, b, e, d);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }, [s, proj]);
  return (
    <>
      <mesh geometry={geom}>
        <meshStandardMaterial vertexColors roughness={0.95} side={THREE.DoubleSide} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.005, 0]}>
        <planeGeometry args={[w, d]} />
        <meshStandardMaterial color="#174a73" transparent opacity={0.88} roughness={0.25} />
      </mesh>
    </>
  );
}

// ---------------------------------------------------------------- lines (3 merged segment sets by voltage tier)
type Tier = { kv: [number, number]; width: number };
const TIERS: Tier[] = [{ kv: [0, 180], width: 1.4 }, { kv: [180, 400], width: 2.4 }, { kv: [400, 9999], width: 3.6 }];

function useLineGeometry(s: LAStatic, proj: Proj, height: (la: number, lo: number) => number) {
  return useMemo(() => TIERS.map((tier) => {
    const pts: [number, number, number][] = [];
    const segBranch: number[] = [];
    s.branches.forEach((b, bi) => {
      if (b.kv < tier.kv[0] || b.kv >= tier.kv[1]) return;
      const lift = 0.08 + (b.kv >= 400 ? 0.12 : b.kv >= 180 ? 0.08 : 0.04);
      const g = b.geometry;
      const inb = (la: number, lo: number) => la >= s.bbox.lat_min && la <= s.bbox.lat_max && lo >= s.bbox.lon_min && lo <= s.bbox.lon_max;
      for (let k = 0; k < g.length - 1; k++) {
        const [la1, lo1] = g[k], [la2, lo2] = g[k + 1];
        if (!inb(la1, lo1) || !inb(la2, lo2)) continue;
        pts.push([proj.x(lo1), height(la1, lo1) + lift, proj.z(la1)], [proj.x(lo2), height(la2, lo2) + lift, proj.z(la2)]);
        segBranch.push(bi);
      }
    });
    return { pts, segBranch, width: tier.width };
  }), [s, proj, height]);
}

function LineTier({ pts, segBranch, width, colors, onHover, onSelect }: {
  pts: [number, number, number][]; segBranch: number[]; width: number; colors: string[];
  onHover: (bi: number | null, p?: THREE.Vector3) => void; onSelect: (bi: number) => void;
}) {
  const vcol = useMemo(() => {
    const out: [number, number, number][] = [];
    const c = new THREE.Color();
    segBranch.forEach((bi) => {
      c.set(colors[bi]);
      out.push([c.r, c.g, c.b], [c.r, c.g, c.b]);
    });
    return out;
  }, [segBranch, colors]);
  if (!pts.length) return null;
  const pick = (e: ThreeEvent<PointerEvent | MouseEvent>) => {
    const fi = (e as unknown as { faceIndex?: number }).faceIndex ?? (e.intersections[0] as unknown as { faceIndex?: number })?.faceIndex;
    return fi != null ? segBranch[fi] : null;
  };
  return (
    <Line points={pts} segments vertexColors={vcol} lineWidth={width}
      onPointerMove={(e) => { e.stopPropagation(); const bi = pick(e); if (bi != null) { document.body.style.cursor = "pointer"; onHover(bi, e.point.clone()); } }}
      onPointerOut={() => { document.body.style.cursor = "default"; onHover(null); }}
      onClick={(e) => { e.stopPropagation(); const bi = pick(e); if (bi != null) onSelect(bi); }} />
  );
}

// ---------------------------------------------------------------- weather arrows
function WindArrows({ hour, proj, height }: { hour: Hour; proj: Proj; height: (la: number, lo: number) => number }) {
  const w = hour.weather;
  const items: { p: [number, number, number]; rot: number; s: number; c: string }[] = [];
  w.lats.forEach((la, j) => w.lons.forEach((lo, i) => {
    const g = w.gust[j][i], dir = w.dir[j][i];
    const toward = ((dir + 180) * Math.PI) / 180; // meteorological "from" -> blowing toward
    const s = 0.5 + Math.min(g, 35) / 12;
    const c = g >= 20 ? "#f0abfc" : g >= 13 ? "#67e8f9" : "#e2e8f0";
    items.push({ p: [proj.x(lo), height(la, lo) + 1.6, proj.z(la)], rot: Math.atan2(Math.sin(toward), -Math.cos(toward)), s, c });
  }));
  return (
    <>
      {items.map((a, k) => (
        <group key={k} position={a.p} rotation={[0, a.rot, 0]}>
          <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, 0.6 * a.s]}>
            <coneGeometry args={[0.35 * a.s, 1.3 * a.s, 8]} />
            <meshBasicMaterial color={a.c} transparent opacity={0.85} />
          </mesh>
          <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, -0.5 * a.s]}>
            <cylinderGeometry args={[0.08 * a.s, 0.08 * a.s, 1.4 * a.s, 6]} />
            <meshBasicMaterial color={a.c} transparent opacity={0.7} />
          </mesh>
        </group>
      ))}
    </>
  );
}

// ---------------------------------------------------------------- fire + red flag
function Flame() {
  const r = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (r.current) r.current.scale.set(1, 0.8 + 0.3 * Math.sin(clock.elapsedTime * 11) + 0.1 * Math.sin(clock.elapsedTime * 27), 1);
  });
  return (
    <group ref={r}>
      <mesh position={[0, 1.2, 0]}><coneGeometry args={[0.9, 2.6, 10]} /><meshBasicMaterial color="#f97316" /></mesh>
      <mesh position={[0.4, 0.9, 0.2]}><coneGeometry args={[0.5, 1.7, 10]} /><meshBasicMaterial color="#facc15" /></mesh>
      <pointLight color="#ff7a1a" distance={20} intensity={40} position={[0, 2, 0]} />
    </group>
  );
}

function ringPoints(ring: [number, number][], proj: Proj, height: (la: number, lo: number) => number, lift = 0.25) {
  return ring.map(([lon, lat]) => [proj.x(lon), height(lat, lon) + lift, proj.z(lat)] as [number, number, number]);
}

// ---------------------------------------------------------------- main scene
export default function LAScene({ s, hour, kind, view, preview, selected, onSelect, overlay }: {
  s: LAStatic; hour: Hour | null; kind: string; view: LAView | null; preview: Set<string> | null;
  selected: string | null; onSelect: (id: string) => void; overlay: RefObject<HTMLDivElement | null>;
}) {
  const proj = useMemo(() => makeProj(s), [s]);
  const height = useMemo(() => makeHeight(s), [s]);
  const tiers = useLineGeometry(s, proj, height);
  const [hover, setHover] = useState<{ bi: number; p: THREE.Vector3 } | null>(null);
  const colors = useMemo(() => s.branches.map((b, bi) => {
    if (preview?.has(b.id)) return "#ffffff";
    if (view?.opened.includes(b.id)) return "#334155";
    if (!hour) return "#94a3b8";
    if (b.cable) return "#64748b";
    return kind === "heat" ? loadColor(hour.lines.loading[bi]) : riskColor(hour.lines.risk[bi], hour.line_alarm);
  }), [s, hour, kind, view, preview]);
  const fireEvent = hour?.event;
  const t = hour ? new Date(hour.t_utc).getTime() : 0;
  const ext = { x: proj.x(s.bbox.lon_max) - proj.x(s.bbox.lon_min), z: proj.z(s.bbox.lat_min) - proj.z(s.bbox.lat_max) };
  const selIdx = selected ? s.branches.findIndex((b) => b.id === selected) : -1;

  return (
    <Canvas camera={{ position: [4, ext.x * 0.62, ext.z * 1.22], fov: 45, far: 2000 }} onPointerMissed={() => setHover(null)}>
      <PortalCtx.Provider value={overlay}>
        <color attach="background" args={[kind === "heat" ? "#24140a" : "#0b1020"]} />
        <ambientLight intensity={0.55} />
        <directionalLight position={[-60, 120, 40]} intensity={1.25} color={kind === "heat" ? "#ffd2a0" : "#ffffff"} />
        <hemisphereLight args={["#bcd7ff", "#2a3a1f", 0.3]} />
        <Terrain s={s} proj={proj} />
        {tiers.map((tier, k) => (
          <LineTier key={k} {...tier} colors={colors} onHover={(bi, p) => setHover(bi != null && p ? { bi, p } : null)} onSelect={(bi) => onSelect(s.branches[bi].id)} />
        ))}
        {selIdx >= 0 && (
          <Line points={s.branches[selIdx].geometry.map(([la, lo]) => [proj.x(lo), height(la, lo) + 0.35, proj.z(la)] as [number, number, number])}
            color="#ffffff" lineWidth={5} transparent opacity={0.65} />
        )}
        {hour && kind !== "heat" && <WindArrows hour={hour} proj={proj} height={height} />}
        {hour?.redflag_active.map((u) => (s.redflag_zones[u] ?? []).map((ring, k) => (
          <Line key={u + k} points={ringPoints(ring, proj, height, 0.3)} color="#f43f5e" lineWidth={1.6} dashed dashSize={0.8} gapSize={0.5} transparent opacity={0.85} />
        )))}
        {s.fires.filter((f) => f.event === fireEvent).map((f) => {
          const ig = new Date(f.ignition_utc).getTime();
          const lit = t >= ig - 30 * 60 * 1000;
          const p: [number, number, number] = [proj.x(f.lon), height(f.lat, f.lon), proj.z(f.lat)];
          const hrs = Math.round((ig - t) / 3600000);
          return (
            <group key={f.id}>
              <group position={p}>
                {lit ? <Flame /> : (
                  <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.2, 0]}><torusGeometry args={[1.0, 0.12, 8, 32]} /><meshBasicMaterial color="#fb923c" /></mesh>
                )}
                <Label position={[0, 4, 0]} center distanceFactor={90} style={{ pointerEvents: "none" }}>
                  <div className={"callout" + (lit ? " fire" : "")} style={{ borderColor: "#f97316" }}>
                    {lit ? "🔥" : "⏳"} {f.name} {lit ? "(ignited)" : `(ignites in ${hrs} h)`}
                  </div>
                </Label>
              </group>
              {lit && f.perimeter && <Line points={ringPoints(f.perimeter, proj, height, 0.2)} color="#fb923c" lineWidth={2} />}
            </group>
          );
        })}
        {s.labels.filter((l, k, arr) => l.lat <= s.bbox.lat_max && l.lat >= s.bbox.lat_min && l.lon <= s.bbox.lon_max && l.lon >= s.bbox.lon_min
          && arr.slice(0, k).every((o) => Math.hypot(o.lat - l.lat, o.lon - l.lon) > 0.12)).slice(0, 8).map((l) => (
          <Label key={l.id} position={[proj.x(l.lon), height(l.lat, l.lon) + 1.2, proj.z(l.lat)]} center distanceFactor={70} style={{ pointerEvents: "none" }}>
            <span className="lbl">{l.name} {Math.round(l.kv)} kV</span>
          </Label>
        ))}
        {s.critical_sites.filter((c) => c.type === "hospital").map((c, k) => (
          <mesh key={k} position={[proj.x(c.lon), height(c.lat, c.lon) + 0.3, proj.z(c.lat)]}>
            <boxGeometry args={[0.35, 0.35, 0.35]} /><meshBasicMaterial color="#f87171" />
          </mesh>
        ))}
        {hover && hour && (() => {
          const b = s.branches[hover.bi], L = hour.lines, i = hover.bi;
          return (
            <Label position={[hover.p.x, hover.p.y + 1.5, hover.p.z]} style={{ pointerEvents: "none" }} zIndexRange={[100, 50]}>
              <div className="tooltip">
                <b>{b.id}</b> <span className="muted">{Math.round(b.kv)} kV · {b.zone} · {b.length_km} km{b.cable ? " · cable" : ""}</span>
                {b.name && <div className="muted">{b.name}</div>}
                <div>Fire risk {L.risk[i].toFixed(2)} (alarm {hour.line_alarm.toFixed(2)}) · gust {L.gust[i]} m/s · RH {L.rh[i]}%</div>
                <div>Flow {L.flow[i]} MW · loading {L.loading[i]}% · conductor {L.temp[i]} °C</div>
                <div>Clearance to vegetation {L.clear[i]} m</div>
                <div className="muted">modeled ratings/impedance · click to pin</div>
              </div>
            </Label>
          );
        })()}
        <OrbitControls target={[3, 0, 8]} maxPolarAngle={Math.PI / 2.1} minDistance={10} maxDistance={400} />
      </PortalCtx.Provider>
    </Canvas>
  );
}
