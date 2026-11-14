import { Html } from "@react-three/drei";
import { createContext, useContext, type RefObject } from "react";
import type { Bus, Line, Terrain } from "../api";

// All <Html> share one stable portal container. Without it drei re-creates each label's React root when the
// canvas event layer connects, which drops labels and spams unmount warnings under React 19.
export const PortalCtx = createContext<RefObject<HTMLDivElement | null> | null>(null);
export function Label(props: React.ComponentProps<typeof Html>) {
  const portal = useContext(PortalCtx);
  return <Html {...props} portal={portal as React.MutableRefObject<HTMLElement>} />;
}

// meters -> scene units: 10 km plane becomes 100 units; vertical exaggerated so the ridge reads.
export const H = 0.012; // vertical scale (1200 m -> 14.4 units)
export const TOWER = 1.0; // visual tower height
export const SAG_VIS = 0.075; // sag exaggeration (~3x at this scale)
export const BLOW_VIS_M = 25; // each meter of wind blowout is drawn as 25 m sideways so the swing is visible
export const sx = (x: number) => x / 100 - 50;
export const sz = (y: number) => 50 - y / 100;

export function makeSampler(t: Terrain) {
  const n = t.n, step = t.size_m / (n - 1);
  return (x: number, y: number) => {
    const fx = Math.min(n - 1.001, Math.max(0, x / step));
    const fy = Math.min(n - 1.001, Math.max(0, y / step));
    const i = Math.floor(fx), j = Math.floor(fy), u = fx - i, v = fy - j;
    const h = t.heights;
    return (h[j][i] * (1 - u) + h[j][i + 1] * u) * (1 - v) + (h[j + 1][i] * (1 - u) + h[j + 1][i + 1] * u) * v;
  };
}
export type Sampler = ReturnType<typeof makeSampler>;
export const groundY = (s: Sampler, x: number, y: number) => Math.max(0, s(x, y)) * H;

/** Midpoint of a line in meters plus its right-hand unit normal (the side positive blowout swings toward). */
export function lineMid(line: Line, buses: Record<string, Bus>) {
  const a = buses[line.from_bus], b = buses[line.to_bus];
  const dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy) || 1;
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, nx: dy / L, ny: -dx / L };
}
