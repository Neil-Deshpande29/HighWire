import { NO_CONDITIONS, type Conditions } from "../la/api";

const ROWS: { k: keyof Conditions; label: string; min: number; max: number; step: number; unit: (v: number) => string }[] = [
  { k: "gust_pct", label: "Wind", min: -50, max: 100, step: 5, unit: (v) => `${v > 0 ? "+" : ""}${v}%` },
  { k: "rh_pct", label: "Humidity", min: -60, max: 100, step: 5, unit: (v) => `${v > 0 ? "+" : ""}${v}%` },
  { k: "temp_c", label: "Temperature", min: -10, max: 10, step: 1, unit: (v) => `${v > 0 ? "+" : ""}${v} °C (${v > 0 ? "+" : ""}${Math.round(v * 1.8)} °F)` },
  { k: "demand_pct", label: "Demand", min: -30, max: 30, step: 5, unit: (v) => `${v > 0 ? "+" : ""}${v}%` },
];

export default function ConditionsPanel({ value, onChange }: { value: Conditions; onChange: (c: Conditions) => void }) {
  const changed = ROWS.some((r) => value[r.k] !== 0);
  return (
    <div className="conditions glass study">
      <div className="tray-head"><b>Conditions</b>{changed && <button className="link small" onClick={() => onChange(NO_CONDITIONS)}>Reset</button>}</div>
      {ROWS.map((r) => (
        <label key={r.k} className="cond-row">
          <span>{r.label}</span>
          <input type="range" min={r.min} max={r.max} step={r.step} value={value[r.k]} onChange={(e) => onChange({ ...value, [r.k]: +e.target.value })} />
          <em className={value[r.k] ? "changed" : ""}>{r.unit(value[r.k])}</em>
        </label>
      ))}
      <div className="muted small">Scales this hour's weather and demand, then reruns the same physics. No model is changed.</div>
    </div>
  );
}
