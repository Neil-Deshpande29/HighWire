import { useState } from "react";
import { C, HOSPITAL, type Layers } from "../la/LAScene";

export type ColorLayer = "fire" | "loading" | "storm";
const COLOR_LABEL: Record<ColorLayer, { name: string; levels: [string, string, string] }> = {
  fire: { name: "Fire risk", levels: ["Normal", "Elevated", "High: act now"] },
  loading: { name: "Line loading", levels: ["Normal", "Above 80%", "Over rating"] },
  storm: { name: "Storm damage", levels: ["Unlikely", "Possible", "Likely"] },
};

export function Legend({ color, anyOff, hospitals }: { color: ColorLayer; anyOff: boolean; hospitals: boolean }) {
  const L = COLOR_LABEL[color];
  return (
    <div className="legend glass">
      <b>{L.name}</b>
      <span><i style={{ background: C.normal }} />{L.levels[0]}</span>
      <span><i style={{ background: C.elevated }} />{L.levels[1]}</span>
      <span><i style={{ background: C.act }} />{L.levels[2]}</span>
      {anyOff && <span><i style={{ background: C.off }} />Off</span>}
      {hospitals && <span><i className="dot" style={{ background: HOSPITAL }} />Hospital</span>}
    </div>
  );
}

export default function LayersMenu({ color, layers, hasStorm, imagerySource, onColor, onLayers }: {
  color: ColorLayer; layers: Layers; hasStorm: boolean; imagerySource: string | null; onColor: (c: ColorLayer) => void; onLayers: (l: Layers) => void;
}) {
  const [open, setOpen] = useState(false);
  const toggle = (k: keyof Layers, label: string) => (
    <label className="layer-row"><input type="checkbox" checked={layers[k]} onChange={() => onLayers({ ...layers, [k]: !layers[k] })} />{label}</label>
  );
  return (
    <div className="layers">
      <button className="map-btn glass" onClick={() => setOpen((o) => !o)} aria-label="Layers">◫ Layers</button>
      {open && (
        <div className="layers-menu glass">
          <div className="eyebrow">Color lines by</div>
          {(["fire", "loading", "storm"] as ColorLayer[]).filter((c) => c !== "storm" || hasStorm).map((c) => (
            <label key={c} className="layer-row"><input type="radio" checked={color === c} onChange={() => onColor(c)} />{COLOR_LABEL[c].name}</label>
          ))}
          <div className="eyebrow">Show</div>
          {toggle("redflag", "Red Flag zones (NWS)")}
          {toggle("hospitals", "Hospitals")}
          {toggle("wind", "Wind")}
          {toggle("labels", "Labels on riskiest lines")}
          {imagerySource
            ? toggle("imagery", `Aerial imagery (${imagerySource})`)
            : <div className="layer-row muted">Aerial imagery: not built yet (make imagery)</div>}
        </div>
      )}
    </div>
  );
}
