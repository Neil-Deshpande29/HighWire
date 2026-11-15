import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Line, MapControls } from "@react-three/drei";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import * as THREE from "three";
import { Label, PortalCtx } from "../components/sceneUtils";
import type { Hazard, Hour, LAStatic, LiveAlert, Quake } from "./api";
import { AlertCurtain, HazardMark, LiveAlerts, QuakeMark } from "./Hazards";

// Projection: 1 scene unit = 1 km. North is -z. Heights are stretched so the mountains read as mountains from above:
// the basin is flat and the ranges around it are where the wind and the fire risk are.
const VEX = 4.6;
const BASE = -1.6; // how far the cut edges of the terrain block reach down, in scene units
const KM_LAT = 110.54;

export type Proj = { x: (lon: number) => number; z: (lat: number) => number };
function makeProj(s: LAStatic): Proj {
  const kx = 111.32 * Math.cos((s.proj.lat0 * Math.PI) / 180);
  return { x: (lon) => (lon - s.proj.lon0) * kx, z: (lat) => -(lat - s.proj.lat0) * KM_LAT };
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

// ---------------------------------------------------------------- color scales: 3 levels (normal / elevated / act now)
export const C = { normal: "#4fae7e", elevated: "#ff9500", act: "#ff3b30", off: "#2c2c2e", cable: "#b9bcc4", none: "#9aa3ad" };
export const HOSPITAL = "#5856d6"; // indigo, so hospitals never read as "act now"
export const riskColor = (r: number, alarm: number) => (r >= alarm ? C.act : r >= 0.6 * alarm ? C.elevated : C.normal);
export const loadColor = (p: number) => (p > 100 ? C.act : p >= 80 ? C.elevated : C.normal);
export const damageColor = (p: number) => (p >= 0.5 ? C.act : p >= 0.25 ? C.elevated : C.normal);

// ---------------------------------------------------------------- terrain (Apple Maps light palette)
/** Loads the aerial picture once it is wanted; null until it has arrived (or if it cannot be loaded). */
function useImagery(url: string | null): THREE.Texture | null {
  const [tex, setTex] = useState<THREE.Texture | null>(null);
  useEffect(() => {
    if (!url) { setTex(null); return; }
    let live = true;
    new THREE.TextureLoader().load(url, (t) => {
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 8;
      if (live) setTex(t); else t.dispose();
    }, undefined, () => { if (live) setTex(null); });
    return () => { live = false; };
  }, [url]);
  return tex;
}

export type Surround = { url: string; bbox: { lat_min: number; lat_max: number; lon_min: number; lon_max: number } };

function Terrain({ s, proj, imagery, surround }: { s: LAStatic; proj: Proj; imagery: string | null; surround: Surround | null }) {
  const picture = useImagery(imagery);
  const around = useImagery(surround?.url ?? null);
  const sb = surround?.bbox;
  const sw = sb ? proj.x(sb.lon_max) - proj.x(sb.lon_min) : 0, sd = sb ? proj.z(sb.lat_min) - proj.z(sb.lat_max) : 0;
  const sx = sb ? (proj.x(sb.lon_max) + proj.x(sb.lon_min)) / 2 : 0, sz = sb ? (proj.z(sb.lat_min) + proj.z(sb.lat_max)) / 2 : 0;
  const w = proj.x(s.bbox.lon_max) - proj.x(s.bbox.lon_min), d = proj.z(s.bbox.lat_min) - proj.z(s.bbox.lat_max);
  const cx = (proj.x(s.bbox.lon_max) + proj.x(s.bbox.lon_min)) / 2, cz = (proj.z(s.bbox.lat_min) + proj.z(s.bbox.lat_max)) / 2;
  const geom = useMemo(() => {
    const { lats, lons, elev } = s.terrain;
    const ny = lats.length, nx = lons.length;
    const pos = new Float32Array(nx * ny * 3), col = new Float32Array(nx * ny * 3), uv = new Float32Array(nx * ny * 2);
    const stops: [number, string][] = [[-1e9, "#a7d3f5"], [0, "#f5f2eb"], [150, "#f1f0e6"], [500, "#e5ebd9"], [1000, "#d6e5c6"], [1700, "#dfe3d6"], [2300, "#f2f2ef"]];
    const c = new THREE.Color(), tmp = new THREE.Color();
    for (let j = 0; j < ny; j++)
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i, h = elev[j][i];
        pos.set([proj.x(lons[i]), (Math.max(h, 0) / 1000) * VEX, proj.z(lats[j])], k * 3);
        let a = 0;
        while (a < stops.length - 1 && h >= stops[a + 1][0]) a++;
        const b = Math.min(a + 1, stops.length - 1);
        const t = b === a ? 0 : Math.max(0, Math.min(1, (h - stops[a][0]) / Math.max(1, stops[b][0] - stops[a][0])));
        c.set(stops[a][1]).lerp(tmp.set(stops[b][1]), a === 0 ? 0 : t);
        col.set([c.r, c.g, c.b], k * 3);
        uv.set([i / (nx - 1), j / (ny - 1)], k * 2); // the aerial picture covers exactly this grid, south edge at v = 0
      }
    const idx: number[] = [];
    for (let j = 0; j < ny - 1; j++)
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i, b = a + 1, e = a + nx, f = e + 1;
        idx.push(a, b, e, b, f, e);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }, [s, proj]);
  // the cut edges of the block: a wall from the terrain's rim down to the base, so the region is a solid relief model
  // standing in its surroundings and not a sheet floating over them
  const skirt = useMemo(() => {
    const { lats, lons, elev } = s.terrain;
    const ny = lats.length, nx = lons.length;
    const rim: [number, number][] = [];
    for (let i = 0; i < nx; i++) rim.push([0, i]);
    for (let j = 1; j < ny; j++) rim.push([j, nx - 1]);
    for (let i = nx - 2; i >= 0; i--) rim.push([ny - 1, i]);
    for (let j = ny - 2; j >= 0; j--) rim.push([j, 0]);
    const pos: number[] = [], col: number[] = [], idx: number[] = [];
    rim.forEach(([j, i], k) => {
      const x = proj.x(lons[i]), z = proj.z(lats[j]), y = (Math.max(elev[j][i], 0) / 1000) * VEX;
      pos.push(x, y, z, x, BASE, z);
      col.push(0.2, 0.23, 0.26, 0.05, 0.06, 0.08); // a lit rim fading to the dark of the base
      if (k < rim.length - 1) { const a = k * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    g.setIndex(idx);
    return g;
  }, [s, proj]);
  return (
    <>
      <mesh geometry={geom}>
        {picture
          ? <meshLambertMaterial key="picture" map={picture} side={THREE.DoubleSide} />
          : <meshLambertMaterial key="palette" vertexColors side={THREE.DoubleSide} />}
      </mesh>
      <mesh geometry={skirt}>
        <meshBasicMaterial vertexColors side={THREE.DoubleSide} />
      </mesh>
      {/* without a picture the sea inside the region needs its own colour; the picture already shows the sea, and a
          plane over it only punched dark holes into the low ground along the coast */}
      {!picture && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[cx, 0.004, cz]}>
          <planeGeometry args={[w, d]} />
          <meshBasicMaterial color="#a7d3f5" />
        </mesh>
      )}
      {/* the land and sea around the region: the same imagery, flat and darker, with no lines or dots on it */}
      {around && sb && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[sx, -0.05, sz]}>
          <planeGeometry args={[sw, sd]} />
          <meshBasicMaterial map={around} />
        </mesh>
      )}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[cx, -0.4, cz]}>
        <planeGeometry args={[w * 12, d * 12]} />
        <meshBasicMaterial color="#0a0d11" />
      </mesh>
    </>
  );
}

// ---------------------------------------------------------------- zoom: lines thicken and dots stay small as you move in
const ZOOM_REF = 110; // camera distance (km) of the overview; widths and dot sizes are given for this distance
const PICK_PX = 10; // a line can be clicked or hovered this many pixels away from its drawn edge
/** How far the camera is from the point it looks at. */
function zoomDistance(st: { camera: THREE.Camera; controls: unknown }): number {
  const target = (st.controls as { target?: THREE.Vector3 } | null)?.target;
  return target ? st.camera.position.distanceTo(target) : st.camera.position.length();
}
/** Line widths are in pixels, so at a fixed width a line looks thinner and thinner against the ground as you zoom in.
 *  This widens it with the zoom, up to four times, so it stays a thick stroke at every distance. */
function useZoomWidth(base: number) {
  const ref = useRef<{ material: { linewidth: number } } | null>(null);
  useFrame((st) => {
    const m = ref.current?.material;
    if (!m) return;
    const w = base * Math.min(4, Math.max(1, ZOOM_REF / zoomDistance(st)));
    if (Math.abs(m.linewidth - w) > 0.05) m.linewidth = w;
  });
  return ref;
}
/** Lets a click or hover count when it lands near a line, not only on its few pixels. */
function PickTolerance() {
  const raycaster = useThree((st) => st.raycaster);
  useEffect(() => { (raycaster.params as unknown as { Line2: { threshold: number } }).Line2 = { threshold: PICK_PX }; }, [raycaster]);
  return null;
}

// ---------------------------------------------------------------- lines (merged segment sets by voltage tier)
const TIERS = [{ kv: [0, 180], width: 2.4 }, { kv: [180, 400], width: 3.4 }, { kv: [400, 9999], width: 4.6 }];
// wires ride above the ground, higher for higher voltage, so the network has depth against the terrain
const lift = (kv: number) => (kv >= 400 ? 0.75 : kv >= 180 ? 0.5 : 0.28);

function useLineGeometry(s: LAStatic, proj: Proj, height: (la: number, lo: number) => number) {
  return useMemo(() => TIERS.map((tier) => {
    const pts: [number, number, number][] = [];
    const segBranch: number[] = [];
    const inb = (la: number, lo: number) => la >= s.bbox.lat_min && la <= s.bbox.lat_max && lo >= s.bbox.lon_min && lo <= s.bbox.lon_max;
    s.branches.forEach((b, bi) => {
      if (b.kv < tier.kv[0] || b.kv >= tier.kv[1]) return;
      const g = b.geometry;
      for (let k = 0; k < g.length - 1; k++) {
        const [la1, lo1] = g[k], [la2, lo2] = g[k + 1];
        if (!inb(la1, lo1) || !inb(la2, lo2)) continue;
        pts.push([proj.x(lo1), height(la1, lo1) + lift(b.kv), proj.z(la1)], [proj.x(lo2), height(la2, lo2) + lift(b.kv), proj.z(la2)]);
        segBranch.push(bi);
      }
    });
    return { pts, segBranch, width: tier.width };
  }), [s, proj, height]);
}

/** Pylons under the high-voltage lines: thin uprights from the ground to the wire. They carry no information of their
 *  own; they are what makes the wires read as standing in the landscape. */
function Pylons({ s, proj, height }: { s: LAStatic; proj: Proj; height: (la: number, lo: number) => number }) {
  const pts = useMemo(() => {
    const out: [number, number, number][] = [];
    const inb = (la: number, lo: number) => la >= s.bbox.lat_min && la <= s.bbox.lat_max && lo >= s.bbox.lon_min && lo <= s.bbox.lon_max;
    s.branches.forEach((b) => {
      if (b.kv < 180 || b.cable) return;
      const step = Math.max(1, Math.round(b.geometry.length / 14)); // a dozen or so along each line is enough
      for (let k = 0; k < b.geometry.length; k += step) {
        const [la, lo] = b.geometry[k];
        if (!inb(la, lo)) continue;
        const x = proj.x(lo), z = proj.z(la), y = height(la, lo);
        out.push([x, y, z], [x, y + lift(b.kv), z]);
      }
    });
    return out;
  }, [s, proj, height]);
  return pts.length ? <Line points={pts} segments color="#aeb8c4" lineWidth={1} transparent opacity={0.5} raycast={() => null} /> : null;
}

type Pick = (bi: number, x: number, y: number) => void;
function LineTier({ pts, segBranch, width, colors, onHover, onClick }: {
  pts: [number, number, number][]; segBranch: number[]; width: number; colors: string[];
  onHover: (bi: number | null, x?: number, y?: number) => void; onClick: Pick;
}) {
  const vcol = useMemo(() => {
    const out: [number, number, number][] = [];
    const c = new THREE.Color();
    segBranch.forEach((bi) => { c.set(colors[bi]); out.push([c.r, c.g, c.b], [c.r, c.g, c.b]); });
    return out;
  }, [segBranch, colors]);
  const ref = useZoomWidth(width);
  if (!pts.length) return null;
  const pick = (e: ThreeEvent<PointerEvent | MouseEvent>) => {
    const fi = (e as unknown as { faceIndex?: number }).faceIndex ?? (e.intersections[0] as unknown as { faceIndex?: number })?.faceIndex;
    return fi != null ? segBranch[fi] : null;
  };
  return (
    <Line ref={ref as never} points={pts} segments vertexColors={vcol} lineWidth={width}
      onPointerMove={(e) => { e.stopPropagation(); const bi = pick(e); if (bi != null) { document.body.style.cursor = "pointer"; onHover(bi, e.nativeEvent.clientX, e.nativeEvent.clientY); } }}
      onPointerOut={() => { document.body.style.cursor = "default"; onHover(null); }}
      onClick={(e) => { e.stopPropagation(); const bi = pick(e); if (bi != null) onClick(bi, e.nativeEvent.clientX, e.nativeEvent.clientY); }} />
  );
}

/** The blue halo on the selected line; it widens with the zoom like the lines under it. */
function SelectedLine({ points }: { points: [number, number, number][] }) {
  const ref = useZoomWidth(9);
  return <Line ref={ref as never} points={points} segments color="#007aff" lineWidth={9} transparent opacity={0.45} />;
}

// ---------------------------------------------------------------- a few faint wind arrows
function WindArrows({ hour, proj, height }: { hour: Hour; proj: Proj; height: (la: number, lo: number) => number }) {
  const segs = useMemo(() => {
    const w = hour.weather, out: [number, number, number][] = [];
    const sj = Math.max(1, Math.round(w.lats.length / 5)), si = Math.max(1, Math.round(w.lons.length / 6));
    for (let j = Math.floor(sj / 2); j < w.lats.length; j += sj)
      for (let i = Math.floor(si / 2); i < w.lons.length; i += si) {
        const la = w.lats[j], lo = w.lons[i], g = w.gust[j][i];
        const toward = ((w.dir[j][i] + 180) * Math.PI) / 180; // meteorological "from" -> blowing toward
        const L = 2 + Math.min(g, 35) / 3.5;
        const dx = Math.sin(toward) * L, dz = -Math.cos(toward) * L;
        const x0 = proj.x(lo) - dx / 2, z0 = proj.z(la) - dz / 2, y = height(la, lo) + 0.9;
        const x1 = x0 + dx, z1 = z0 + dz;
        out.push([x0, y, z0], [x1, y, z1]);
        const a = Math.atan2(dz, dx), hl = 0.28 * L;
        for (const s of [2.6, -2.6]) out.push([x1, y, z1], [x1 + Math.cos(a + s) * hl, y, z1 + Math.sin(a + s) * hl]);
      }
    return out;
  }, [hour, proj, height]);
  return segs.length ? <Line points={segs} segments color="#35587a" lineWidth={1.6} transparent opacity={0.4} /> : null;
}

// ---------------------------------------------------------------- fire (only after "Show what happened")
function Flame() {
  const r = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (r.current) r.current.scale.set(1, 0.8 + 0.3 * Math.sin(clock.elapsedTime * 11) + 0.1 * Math.sin(clock.elapsedTime * 27), 1);
  });
  return (
    <group ref={r}>
      <mesh position={[0, 1.2, 0]}><coneGeometry args={[0.9, 2.6, 10]} /><meshBasicMaterial color="#ff6a00" /></mesh>
      <mesh position={[0.4, 0.9, 0.2]}><coneGeometry args={[0.5, 1.7, 10]} /><meshBasicMaterial color="#ffcc00" /></mesh>
    </group>
  );
}

const ringPoints = (ring: [number, number][], proj: Proj, height: (la: number, lo: number) => number, up = 0.25) =>
  ring.map(([lon, lat]) => [proj.x(lon), height(lat, lon) + up, proj.z(lat)] as [number, number, number]);
/** Polygon outline as segment pairs, keeping only the parts inside the modeled area. */
function ringSegments(ring: [number, number][], s: LAStatic, proj: Proj, height: (la: number, lo: number) => number, up = 0.3) {
  const b = s.bbox, inb = ([lo, la]: [number, number]) => la >= b.lat_min && la <= b.lat_max && lo >= b.lon_min && lo <= b.lon_max;
  const out: [number, number, number][] = [];
  for (let k = 0; k < ring.length - 1; k++)
    if (inb(ring[k]) && inb(ring[k + 1])) out.push(...ringPoints([ring[k], ring[k + 1]], proj, height, up));
  return out;
}

// ---------------------------------------------------------------- clickable substations, hospitals (instanced)
function Points({ items, proj, height, color, size, onClick, y = 0.25 }: {
  items: { lat: number; lon: number; id: string }[]; proj: Proj; height: (la: number, lo: number) => number;
  color: string; size: number; onClick?: (id: string, x: number, y: number) => void; y?: number;
}) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const scale = useRef(1);
  const place = (k: number) => { // dots are sized in km; shrinking them as the camera moves in keeps them small on screen
    const m = ref.current;
    if (!m) return;
    const o = new THREE.Object3D();
    items.forEach((p, n) => { o.position.set(proj.x(p.lon), height(p.lat, p.lon) + y, proj.z(p.lat)); o.scale.setScalar(k); o.updateMatrix(); m.setMatrixAt(n, o.matrix); });
    m.instanceMatrix.needsUpdate = true;
    m.boundingSphere = null; // recomputed on the next pick, so clicks still land
  };
  useLayoutEffect(() => { place(scale.current); }, [items, proj, height, y]); // eslint-disable-line react-hooks/exhaustive-deps
  useFrame((st) => {
    const k = Math.min(1, Math.max(0.1, zoomDistance(st) / ZOOM_REF));
    if (Math.abs(k - scale.current) / scale.current < 0.04) return;
    scale.current = k;
    place(k);
  });
  if (!items.length) return null;
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, items.length]}
      onClick={onClick ? (e) => { e.stopPropagation(); if (e.instanceId != null) onClick(items[e.instanceId].id, e.nativeEvent.clientX, e.nativeEvent.clientY); } : undefined}
      onPointerOver={onClick ? () => { document.body.style.cursor = "pointer"; } : undefined}
      onPointerOut={onClick ? () => { document.body.style.cursor = "default"; } : undefined}>
      <cylinderGeometry args={[size, size, size * 0.6, 14]} />
      <meshBasicMaterial color={color} />
    </instancedMesh>
  );
}

// ---------------------------------------------------------------- camera framing
export type Focus = { lat: number; lon: number; spanKm: number; key: number };
function CameraRig({ focus, proj }: { focus: Focus | null; proj: Proj }) {
  const camera = useThree((st) => st.camera);
  const controls = useThree((st) => st.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  const anim = useRef<{ fT: THREE.Vector3; tT: THREE.Vector3; fP: THREE.Vector3; tP: THREE.Vector3; t: number } | null>(null);
  useEffect(() => {
    if (!focus || !controls) return;
    const d = Math.max(28, focus.spanKm * 1.15);
    const tx = proj.x(focus.lon) + d * 0.04, tz = proj.z(focus.lat); // panels sit on both sides, the right one a little wider
    // look in from the south at a slant, so the ground rises toward the back and the relief shows
    anim.current = { fT: controls.target.clone(), tT: new THREE.Vector3(tx, 0, tz), fP: camera.position.clone(), tP: new THREE.Vector3(tx, d * 0.8, tz + d * 0.88), t: 0 };
  }, [focus?.key, controls]); // eslint-disable-line react-hooks/exhaustive-deps
  useFrame((_, dt) => {
    const a = anim.current;
    if (!a || !controls) return;
    a.t = Math.min(1, a.t + dt / 0.9);
    const e = 1 - Math.pow(1 - a.t, 3);
    controls.target.lerpVectors(a.fT, a.tT, e);
    camera.position.lerpVectors(a.fP, a.tP, e);
    controls.update();
    if (a.t >= 1) anim.current = null;
  });
  return null;
}

/** Buttons outside the canvas ask for a move; this turns, tilts or zooms the camera around the point it looks at. */
export type Nav = { cmd: "in" | "out" | "left" | "right" | "up" | "down"; key: number };
const MAX_TILT = Math.PI / 2.12;
function NavRig({ nav }: { nav: Nav | null }) {
  const camera = useThree((st) => st.camera);
  const controls = useThree((st) => st.controls) as unknown as { target: THREE.Vector3; update: () => void } | null;
  const anim = useRef<{ from: THREE.Spherical; to: THREE.Spherical; t: number } | null>(null);
  useEffect(() => {
    if (!nav || !controls) return;
    const from = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    const to = from.clone();
    if (nav.cmd === "in") to.radius = Math.max(8, from.radius * 0.65);
    if (nav.cmd === "out") to.radius = Math.min(420, from.radius * 1.5);
    if (nav.cmd === "left") to.theta = from.theta - Math.PI / 8;
    if (nav.cmd === "right") to.theta = from.theta + Math.PI / 8;
    if (nav.cmd === "up") to.phi = Math.max(0.12, from.phi - 0.22);
    if (nav.cmd === "down") to.phi = Math.min(MAX_TILT, from.phi + 0.22);
    anim.current = { from, to, t: 0 };
  }, [nav?.key, controls]); // eslint-disable-line react-hooks/exhaustive-deps
  useFrame((_, dt) => {
    const a = anim.current;
    if (!a || !controls) return;
    a.t = Math.min(1, a.t + dt / 0.35);
    const e = 1 - Math.pow(1 - a.t, 3);
    const sph = new THREE.Spherical(a.from.radius + (a.to.radius - a.from.radius) * e, a.from.phi + (a.to.phi - a.from.phi) * e, a.from.theta + (a.to.theta - a.from.theta) * e);
    camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(sph));
    controls.update();
    if (a.t >= 1) anim.current = null;
  });
  return null;
}

/** Lines that pick up power when something is switched off: a blue halo, widening with the zoom like the lines under it. */
function ImpactLines({ points }: { points: [number, number, number][] }) {
  const ref = useZoomWidth(8);
  return <Line ref={ref as never} points={points} segments color="#59c2ff" lineWidth={8} transparent opacity={0.55} />;
}

// ---------------------------------------------------------------- main scene
export type Layers = { wind: boolean; redflag: boolean; hospitals: boolean; labels: boolean; imagery: boolean };

export default function LAScene({ s, hour, colors, layers, imagery, surround, nav, impact, labelIds, selected, dimBuses, showFires, focus, overlay, hazards, alerts, quakes, onHazard, onHoverLine, onClickLine, onClickBus, onMiss }: {
  s: LAStatic; hour: Hour | null; colors: string[]; layers: Layers; imagery: string | null; surround: Surround | null; nav: Nav | null;
  /** where this hour's numbers show trouble, and (live view only) what the warning services say right now */
  hazards: Hazard[]; alerts: LiveAlert[]; quakes: Quake[]; onHazard: (h: Hazard) => void;
  /** lines whose load changed because of a switch: branch index and the label to draw on it ("+18%") */
  impact: { bi: number; text: string }[]; labelIds: string[]; selected: string | null;
  dimBuses: string[]; showFires: boolean; focus: Focus | null; overlay: RefObject<HTMLDivElement | null>;
  onHoverLine: (bi: number | null, x?: number, y?: number) => void; onClickLine: Pick; onClickBus: (id: string, x: number, y: number) => void; onMiss: () => void;
}) {
  const proj = useMemo(() => makeProj(s), [s]);
  const height = useMemo(() => makeHeight(s), [s]);
  const tiers = useLineGeometry(s, proj, height);
  const t = hour ? new Date(hour.t_utc).getTime() : 0;
  const ext = { x: proj.x(s.bbox.lon_max) - proj.x(s.bbox.lon_min), z: proj.z(s.bbox.lat_min) - proj.z(s.bbox.lat_max) };
  const selIdx = selected ? s.branches.findIndex((b) => b.id === selected) : -1;
  const selSeg = useMemo(() => (selIdx < 0 ? [] : ringSegments(s.branches[selIdx].geometry.map(([la, lo]) => [lo, la] as [number, number]), s, proj, height, 0.35)), [selIdx, s, proj, height]);
  const impactSeg = useMemo(() => impact.flatMap(({ bi }) => ringSegments(s.branches[bi].geometry.map(([la, lo]) => [lo, la] as [number, number]), s, proj, height, 0.3)),
    [impact, s, proj, height]);
  const subs = useMemo(() => s.buses.filter((b) => b.kind === "substation").map((b) => ({ id: b.id, lat: b.lat, lon: b.lon })), [s]);
  const hosp = useMemo(() => s.critical_sites.filter((c) => c.type === "hospital").map((c, k) => ({ id: String(k), lat: c.lat, lon: c.lon })), [s]);
  const busById = useMemo(() => new Map(s.buses.map((b) => [b.id, b])), [s]);
  const inside = useMemo(() => (la: number, lo: number) => la >= s.bbox.lat_min && la <= s.bbox.lat_max && lo >= s.bbox.lon_min && lo <= s.bbox.lon_max, [s]);
  const mid = (bi: number) => { const g = s.branches[bi].geometry; const [la, lo] = g[Math.floor(g.length / 2)]; return [proj.x(lo), height(la, lo) + 1.0, proj.z(la)] as [number, number, number]; };

  return (
    <Canvas camera={{ position: [0, ext.x * 0.46, ext.z * 0.98], fov: 40, far: 3000 }} onPointerMissed={onMiss}>
      <PortalCtx.Provider value={overlay}>
        <color attach="background" args={["#0a0d11"]} />
        <fog attach="fog" args={["#0a0d11", 300, 1100]} />
        {/* a low sun from the north-west and a dim sky: slopes facing it light up and the far sides fall into shade */}
        <ambientLight intensity={0.85} />
        <hemisphereLight args={["#dbe7ff", "#1b2026", 0.7]} />
        <directionalLight position={[-150, 85, -110]} intensity={2.3} />
        <Terrain s={s} proj={proj} imagery={layers.imagery ? imagery : null} surround={layers.imagery ? surround : null} />
        <PickTolerance />
        <Pylons s={s} proj={proj} height={height} />
        {tiers.map((tier, k) => <LineTier key={k} {...tier} colors={colors} onHover={onHoverLine} onClick={onClickLine} />)}
        {impactSeg.length > 0 && <ImpactLines points={impactSeg} />}
        {selSeg.length > 0 && <SelectedLine points={selSeg} />}
        {hour && layers.wind && <WindArrows hour={hour} proj={proj} height={height} />}
        {hour && layers.redflag && hour.redflag_active.map((u, n) => (
          <AlertCurtain key={u} rings={s.redflag_zones[u] ?? []} kind="fire" label={n === 0 ? `Red Flag Warning${hour.redflag_active.length > 1 ? ` · ${hour.redflag_active.length} zones` : ""}` : undefined}
            proj={proj} height={height} inside={inside} />
        ))}
        <LiveAlerts alerts={alerts} proj={proj} height={height} inside={inside} />
        {quakes.map((q) => <QuakeMark key={q.id} q={q} proj={proj} height={height} />)}
        {hazards.map((hz) => <HazardMark key={hz.id} hz={hz} proj={proj} height={height} onPick={onHazard} />)}
        <Points items={subs} proj={proj} height={height} color="#ffffff" size={0.2} onClick={onClickBus} />
        {layers.hospitals && <Points items={hosp} proj={proj} height={height} color={HOSPITAL} size={0.22} y={0.45} />}
        {dimBuses.map((id) => {
          const b = busById.get(id);
          return b ? (
            <mesh key={id} rotation={[-Math.PI / 2, 0, 0]} position={[proj.x(b.lon), height(b.lat, b.lon) + 0.06, proj.z(b.lat)]}>
              <circleGeometry args={[2.2, 28]} /><meshBasicMaterial color="#1c1c1e" transparent opacity={0.2} depthWrite={false} />
            </mesh>
          ) : null;
        })}
        {showFires && hour && s.fires.filter((f) => f.event === hour.event).map((f) => {
          const ig = new Date(f.ignition_utc).getTime();
          const lit = t >= ig - 30 * 60 * 1000;
          return (
            <group key={f.id}>
              <group position={[proj.x(f.lon), height(f.lat, f.lon), proj.z(f.lat)]}>
                {lit ? <Flame /> : <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.2, 0]}><torusGeometry args={[0.9, 0.14, 8, 32]} /><meshBasicMaterial color="#ff6a00" /></mesh>}
                <Label position={[0, 3.6, 0]} center zIndexRange={[4, 0]} style={{ pointerEvents: "none" }}>
                  <div className="map-pill fire">{f.name.replace(/ Fire$/, "")} fire {lit ? "" : `starts here at ${new Date(f.ignition_utc).toLocaleTimeString("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit" })}`}</div>
                </Label>
              </group>
              {lit && f.perimeter && <Line points={ringPoints(f.perimeter, proj, height, 0.2)} color="#ff6a00" lineWidth={2} />}
            </group>
          );
        })}
        {impact.map(({ bi, text }) => (
          <Label key={`i${bi}`} position={mid(bi)} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
            <span className="map-pill shift">{s.branches[bi].id} {text}</span>
          </Label>
        ))}
        {layers.labels && labelIds.map((id) => {
          const bi = s.branches.findIndex((b) => b.id === id);
          return bi >= 0 ? (
            <Label key={id} position={mid(bi)} center zIndexRange={[4, 0]} style={{ pointerEvents: "none" }}>
              <span className="map-pill">{id}</span>
            </Label>
          ) : null;
        })}
        <MapControls makeDefault maxPolarAngle={MAX_TILT} minDistance={8} maxDistance={420} screenSpacePanning={false} />
        <CameraRig focus={focus} proj={proj} />
        <NavRig nav={nav} />
      </PortalCtx.Provider>
    </Canvas>
  );
}
