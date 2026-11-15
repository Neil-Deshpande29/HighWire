import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { BuildingsOut } from "../api";
import { fmtInt } from "../ui/format";

type Toured = NonNullable<BuildingsOut["tours"]>[number];
const KIND: Record<string, string> = { senior_housing: "senior housing", residential: "apartments", medical: "medical offices", hospital: "hospital", school: "school",
  lodging: "hotel", industrial: "industrial", office: "offices", retail: "shops", data_center: "data center", cold_storage: "cold storage" };

/** A Matterport 3D tour of one building, over the screen. The tours are the ones CoStar holds of each property; they are
 *  public, so the viewer needs no key. Escape or the backdrop closes it. */
function TourPanel({ site, onClose }: { site: Toured; onClose: () => void }) {
  const [k, setK] = useState(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="tour-back" role="dialog" aria-modal="true" aria-label={`3D tour of ${site.name}`} onClick={onClose}>
      <div className="tour glass" onClick={(e) => e.stopPropagation()}>
        <div className="tour-head">
          <div>
            <b>{site.name}</b>
            <span className="muted small"> · {KIND[site.kind] ?? site.kind}{site.units ? ` · ${fmtInt(site.units)} homes` : ""} · would lose grid power</span>
          </div>
          <span className="spacer" />
          {site.models.length > 1 && (
            <div className="segmented small" role="group" aria-label="Which tour">
              {site.models.map((m, n) => <button key={m} className={n === k ? "on" : ""} aria-pressed={n === k} onClick={() => setK(n)}>Tour {n + 1}</button>)}
            </div>
          )}
          <button className="x" onClick={onClose} aria-label="Close the tour">×</button>
        </div>
        <iframe key={site.models[k]} title={`Matterport 3D tour of ${site.name}`} src={`https://my.matterport.com/show/?m=${encodeURIComponent(site.models[k])}&play=1&qs=1&brand=0&help=0`}
          allow="fullscreen; xr-spatial-tracking" allowFullScreen />
        <div className="muted small">3D tour by Matterport, from the CoStar data kit. It shows what is inside a building that would lose power; it changes no number.</div>
      </div>
    </div>, document.body);
}

/** "See inside": the buildings among those losing power that have a 3D tour, the ones that matter most first. */
export default function Tours({ out }: { out: BuildingsOut | null | undefined }) {
  const [open, setOpen] = useState<Toured | null>(null);
  const list = out?.tours ?? [];
  if (!list.length) return null;
  const more = (out?.n_tours ?? list.length) - list.length;
  return (
    <div className="tours">
      <div className="eyebrow">See inside · {out?.n_tours ?? list.length} of these have a 3D tour</div>
      <div className="tour-chips">
        {list.map((t) => (
          <button key={t.name} className="chip-link tour-chip" onClick={() => setOpen(t)} title={`Open the Matterport 3D tour of ${t.name}`}>
            <span aria-hidden="true">◈</span> {t.name}{t.units ? <span className="muted"> · {fmtInt(t.units)} homes</span> : null}
          </button>
        ))}
        {more > 0 && <span className="muted small">and {more} more</span>}
      </div>
      {open && <TourPanel site={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
