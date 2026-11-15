import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { Label } from "../components/sceneUtils";
import type { Hazard, LiveAlert, Quake } from "./api";

/** Hazards in three dimensions: what the data found, drawn where it is.
 *
 *  A detected hazard (backend/la/hazards.py) is a place: a centre, a size, how bad it is and which way the wind blows.
 *  It appears with a burst of quick pulses, then settles into a steady one: rings spreading over the ground, a column of
 *  light that can be seen from across the map, and a shape that says what kind of trouble it is. Fire glows, throws
 *  embers downwind and lays a wedge on the ground where a spark would be carried. Storm damage turns under slanting
 *  rain. An overload pulses amber. A warning from the weather service is a translucent curtain standing on the outline
 *  of its area, and an earthquake ripples out from its epicentre.
 *
 *  Everything here is decoration on top of numbers computed elsewhere: it never changes a line's colour or a count. */

type H = (lat: number, lon: number) => number;
type Proj = { x: (lon: number) => number; z: (lat: number) => number };
const TINT: Record<string, string> = {
  fire: "#ff4d2e", storm: "#6ea8ff", overload: "#ffb020", quake: "#f1e9d2", wind: "#7cc4ff", rain: "#4a9dff", heat: "#ff9500",
  tornado: "#c77dff", hurricane: "#c77dff", weather: "#9fb3c8",
};
const BURST_S = 2.4, BURST_PERIOD = 0.42; // a new hazard announces itself with quick pulses, then slows down
const still = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Rings that spread out over the ground from a centre and fade. Fast for the first moments, then steady. */
function PulseRings({ radius, color, period, count = 3 }: { radius: number; color: string; period: number; count?: number }) {
  const group = useRef<THREE.Group>(null);
  const born = useRef<number | null>(null);
  const quiet = useMemo(still, []);
  useFrame(({ clock }) => {
    const g = group.current;
    if (!g) return;
    if (born.current == null) born.current = clock.elapsedTime;
    const age = clock.elapsedTime - born.current;
    const p = age < BURST_S ? BURST_PERIOD : period;
    g.children.forEach((child, k) => {
      const phase = quiet ? (k + 1) / count : (age / p + k / count) % 1;
      const m = child as THREE.Mesh;
      m.scale.setScalar(radius * (0.12 + 0.88 * phase));
      (m.material as THREE.MeshBasicMaterial).opacity = (quiet ? 0.5 : Math.pow(1 - phase, 1.4)) * (age < BURST_S ? 1 : 0.8);
    });
  });
  return (
    <group ref={group} rotation={[-Math.PI / 2, 0, 0]}>
      {Array.from({ length: count }, (_, k) => (
        <mesh key={k} renderOrder={30}>
          <ringGeometry args={[0.86, 1, 64]} />
          {/* plain blending: over pale ground an additive ring burns out to white and loses its colour */}
          <meshBasicMaterial color={color} transparent opacity={0} depthWrite={false} depthTest={false} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  );
}

/** A column of light standing on the hazard, brightest at the ground, so the place can be found from any distance. */
function Beacon({ height, color, width = 0.55 }: { height: number; color: string; width?: number }) {
  const mat = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(() => ({ uColor: { value: new THREE.Color(color) }, uTime: { value: 0 } }), [color]);
  useFrame(({ clock }) => { if (mat.current) mat.current.uniforms.uTime.value = clock.elapsedTime; });
  return (
    <mesh position={[0, height / 2, 0]} renderOrder={31}>
      <cylinderGeometry args={[width * 0.35, width, height, 20, 1, true]} />
      <shaderMaterial ref={mat} uniforms={uniforms} transparent depthWrite={false} side={THREE.DoubleSide} blending={THREE.AdditiveBlending}
        vertexShader={"varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }"}
        fragmentShader={"uniform vec3 uColor; uniform float uTime; varying vec2 vUv; void main(){ float a = pow(1.0 - vUv.y, 1.6) * (0.5 + 0.2 * sin(uTime * 3.0 - vUv.y * 9.0)); gl_FragColor = vec4(uColor, a); }"} />
    </mesh>
  );
}

/** Fire: a low dome of heat that flickers, embers lifting off and drifting with the wind. */
function FireGlow({ radius, toward }: { radius: number; toward: [number, number] }) {
  const dome = useRef<THREE.Mesh>(null);
  const pts = useRef<THREE.Points>(null);
  const N = 46;
  const seeds = useMemo(() => Array.from({ length: N }, (_, k) => ({ a: (k * 2.399) % (2 * Math.PI), r: 0.15 + 0.85 * ((k * 0.618) % 1), s: 0.5 + ((k * 0.37) % 1), o: (k * 0.731) % 1 })), []);
  const geom = useMemo(() => { const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 3), 3)); return g; }, []);
  const quiet = useMemo(still, []);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (dome.current) {
      const f = quiet ? 1 : 0.9 + 0.12 * Math.sin(t * 9) + 0.06 * Math.sin(t * 23);
      dome.current.scale.set(radius * 0.55 * f, radius * 0.28 * f, radius * 0.55 * f);
      (dome.current.material as THREE.MeshBasicMaterial).opacity = 0.2 + (quiet ? 0 : 0.07 * Math.sin(t * 13));
    }
    const pos = geom.attributes.position as THREE.BufferAttribute;
    seeds.forEach((sd, k) => {
      const life = quiet ? sd.o : (t * 0.22 * sd.s + sd.o) % 1; // rises, drifts downwind, starts again
      const r = sd.r * radius * 0.5;
      pos.setXYZ(k, Math.cos(sd.a) * r + toward[0] * life * radius * 1.3, 0.2 + life * radius * 0.75, Math.sin(sd.a) * r + toward[1] * life * radius * 1.3);
    });
    pos.needsUpdate = true;
  });
  return (
    <>
      <mesh ref={dome} renderOrder={29}>
        <sphereGeometry args={[1, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2]} />
        <meshBasicMaterial color="#ff7a1a" transparent opacity={0.2} depthWrite={false} blending={THREE.AdditiveBlending} side={THREE.DoubleSide} />
      </mesh>
      <points ref={pts} geometry={geom} renderOrder={32}>
        <pointsMaterial color="#ffc247" size={3.2} sizeAttenuation={false} transparent opacity={0.95} depthWrite={false} blending={THREE.AdditiveBlending} />
      </points>
    </>
  );
}

/** Where the wind would carry a spark: a wedge on the ground, pointing downwind and fading with distance. */
function Reach({ length, toward, color }: { length: number; toward: [number, number]; color: string }) {
  const geom = useMemo(() => {
    const a = Math.atan2(toward[1], toward[0]), half = 0.36, steps = 10;
    const pos: number[] = [0, 0, 0], col: number[] = [], idx: number[] = [];
    const c = new THREE.Color(color);
    col.push(c.r, c.g, c.b, 0.42);
    for (let k = 0; k <= steps; k++) {
      const ang = a - half + (2 * half * k) / steps;
      pos.push(Math.cos(ang) * length, 0, Math.sin(ang) * length);
      col.push(c.r, c.g, c.b, 0);
      if (k < steps) idx.push(0, k + 1, k + 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 4));
    g.setIndex(idx);
    return g;
  }, [length, toward, color]);
  return (
    <mesh geometry={geom} position={[0, 0.12, 0]} renderOrder={28}>
      <meshBasicMaterial vertexColors transparent depthWrite={false} depthTest={false} side={THREE.DoubleSide} />
    </mesh>
  );
}

/** Storm: rain slanting down with the wind inside the area, under an arc that sweeps round like a radar. */
function StormCell({ radius, toward, color }: { radius: number; toward: [number, number]; color: string }) {
  const N = 70, top = Math.max(3, radius * 0.9);
  const seeds = useMemo(() => Array.from({ length: N }, (_, k) => ({ a: (k * 2.399) % (2 * Math.PI), r: Math.sqrt((k * 0.618) % 1), o: (k * 0.413) % 1, s: 0.7 + ((k * 0.29) % 1) * 0.6 })), []);
  const geom = useMemo(() => { const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 6), 3)); return g; }, []);
  const sweep = useRef<THREE.Mesh>(null);
  const quiet = useMemo(still, []);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime, pos = geom.attributes.position as THREE.BufferAttribute;
    seeds.forEach((sd, k) => {
      const fall = quiet ? sd.o : (t * 0.9 * sd.s + sd.o) % 1;
      const y = top * (1 - fall), x = Math.cos(sd.a) * sd.r * radius + toward[0] * fall * 1.6, z = Math.sin(sd.a) * sd.r * radius + toward[1] * fall * 1.6;
      pos.setXYZ(k * 2, x, y, z);
      pos.setXYZ(k * 2 + 1, x + toward[0] * 0.35, y - 0.7, z + toward[1] * 0.35);
    });
    pos.needsUpdate = true;
    if (sweep.current && !quiet) sweep.current.rotation.z = -t * 1.1;
  });
  return (
    <>
      <lineSegments geometry={geom} renderOrder={32}>
        <lineBasicMaterial color={color} transparent opacity={0.75} depthWrite={false} />
      </lineSegments>
      <mesh ref={sweep} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.15, 0]} renderOrder={29}>
        <ringGeometry args={[radius * 0.15, radius, 40, 1, 0, Math.PI / 3]} />
        <meshBasicMaterial color={color} transparent opacity={0.22} depthWrite={false} depthTest={false} side={THREE.DoubleSide} blending={THREE.AdditiveBlending} />
      </mesh>
    </>
  );
}

/** One detected hazard, standing where it is. Click its label to fly to it. */
export function HazardMark({ hz, proj, height, onPick }: { hz: Hazard; proj: Proj; height: H; onPick?: (h: Hazard) => void }) {
  const color = TINT[hz.kind] ?? TINT.weather, act = hz.level === "act";
  const toward = useMemo(() => { const a = ((hz.wind_from_deg + 180) * Math.PI) / 180; return [Math.sin(a), -Math.cos(a)] as [number, number]; }, [hz.wind_from_deg]);
  const y = height(hz.lat, hz.lon), r = hz.radius_km;
  const tall = (act ? 7 : 4.5) + Math.min(5, hz.n_lines * 0.35);
  return (
    <group position={[proj.x(hz.lon), y + 0.2, proj.z(hz.lat)]}>
      <PulseRings radius={r} color={color} period={act ? 1.9 : 3.2} count={act ? 3 : 2} />
      <Beacon height={tall} color={color} width={act ? 0.7 : 0.45} />
      {hz.kind === "fire" && act && <FireGlow radius={r} toward={toward} />}
      {hz.kind === "fire" && <Reach length={Math.max(10, r * 1.6)} toward={toward} color={color} />}
      {hz.kind === "storm" && <StormCell radius={r * 0.8} toward={toward} color={color} />}
      <Label position={[0, tall + 0.6, 0]} center zIndexRange={[6, 0]}>
        <button className={`map-pill hazard ${hz.kind} ${hz.level}`} title={`${hz.detail} Click to fly there.`} onClick={() => onPick?.(hz)}>
          <b>{hz.title}</b><span>{hz.n_lines} line{hz.n_lines === 1 ? "" : "s"}{hz.rising ? " · rising" : ""}</span>
        </button>
      </Label>
    </group>
  );
}

/** A warning area as a translucent curtain standing on its outline, following the ground. */
export function AlertCurtain({ rings, kind, label, proj, height, inside, tall = 1.8 }: {
  rings: [number, number][][]; kind: string; label?: string; proj: Proj; height: H; inside: (lat: number, lon: number) => boolean; tall?: number;
}) {
  const color = TINT[kind] ?? TINT.weather;
  const { geom, top } = useMemo(() => {
    const pos: number[] = [], col: number[] = [], idx: number[] = [];
    const c = new THREE.Color(color);
    let best: [number, number, number] | null = null, bestN = 0;
    rings.forEach((ring) => {
      let n = 0, sx = 0, sy = 0, sz = 0;
      for (let k = 0; k < ring.length - 1; k++) {
        const [lo1, la1] = ring[k], [lo2, la2] = ring[k + 1];
        if (!inside(la1, lo1) || !inside(la2, lo2)) continue;
        const x1 = proj.x(lo1), z1 = proj.z(la1), y1 = height(la1, lo1) + 0.12, x2 = proj.x(lo2), z2 = proj.z(la2), y2 = height(la2, lo2) + 0.12;
        const v = pos.length / 3;
        pos.push(x1, y1, z1, x2, y2, z2, x2, y2 + tall, z2, x1, y1 + tall, z1);
        col.push(c.r, c.g, c.b, 0.5, c.r, c.g, c.b, 0.5, c.r, c.g, c.b, 0, c.r, c.g, c.b, 0);
        idx.push(v, v + 1, v + 2, v, v + 2, v + 3);
        n++; sx += x1; sy += y1; sz += z1;
      }
      if (n > bestN) { bestN = n; best = [sx / n, sy / n + tall + 0.5, sz / n]; }
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 4));
    g.setIndex(idx);
    return { geom: g, top: best as [number, number, number] | null };
  }, [rings, color, proj, height, inside, tall]);
  if (!top) return null;
  return (
    <>
      <mesh geometry={geom} renderOrder={27}>
        <meshBasicMaterial vertexColors transparent depthWrite={false} side={THREE.DoubleSide} />
      </mesh>
      {label && (
        <Label position={top} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
          <span className={`map-pill zone ${kind}`}>{label}</span>
        </Label>
      )}
    </>
  );
}

/** Live warnings from the weather service: each one a curtain around its area, named once. */
export function LiveAlerts({ alerts, proj, height, inside }: { alerts: LiveAlert[]; proj: Proj; height: H; inside: (lat: number, lon: number) => boolean }) {
  return <>{alerts.filter((a) => a.rings.length).map((a) => <AlertCurtain key={a.id} rings={a.rings} kind={a.kind} label={a.event} proj={proj} height={height} inside={inside} tall={2.2} />)}</>;
}

/** A recent earthquake: ripples from its epicentre, wider for a stronger one. */
export function QuakeMark({ q, proj, height }: { q: Quake; proj: Proj; height: H }) {
  const mag = q.mag ?? 2.5, r = Math.max(2.5, (mag - 1.5) * 3.2);
  const mins = Math.max(0, Math.round((Date.now() - new Date(q.time_utc).getTime()) / 60000));
  const ago = mins < 90 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`;
  return (
    <group position={[proj.x(q.lon), height(q.lat, q.lon) + 0.2, proj.z(q.lat)]}>
      <PulseRings radius={r} color={TINT.quake} period={2.6} count={4} />
      <Beacon height={4 + mag} color={TINT.quake} width={0.4} />
      <Label position={[0, 5.2 + mag, 0]} center zIndexRange={[6, 0]} style={{ pointerEvents: "none" }}>
        <span className="map-pill hazard quake act"><b>Earthquake M{mag.toFixed(1)}</b><span>{q.place ?? "near here"} · {ago}</span></span>
      </Label>
    </group>
  );
}
