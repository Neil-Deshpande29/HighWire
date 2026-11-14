import { useEffect, useState } from "react";
import { la, siteImageUrl, type Site } from "./api";

/** Site agent panel: aerial photos along a line (its most fire-prone stretches), data facts per photo, Gemini's
 *  description of each, and every place a description disagrees with the data. Context only; it changes no number. */
export default function SitePanel({ line, eid, i }: { line: string; eid: string; i: number }) {
  const [site, setSite] = useState<Site | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true); setErr(null);
    la.site(line, eid, i)
      .then((s) => { if (live) setSite(s); })
      .catch((e) => { if (live) setErr(String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [line, eid, i]);

  if (err) return <div className="error small">Site agent: {err}</div>;
  if (!site || site.line !== line) return <div className="muted small">{loading ? `Site agent: looking along ${line}…` : ""}</div>;
  const f = site.facts;
  const lk = site.look;
  return (
    <div className="card site">
      <div className="site-head">
        <b>🛰 Site agent · {line}</b>
        <span className="muted small">{Math.round(f.kv)} kV · {f.zone} · {f.length_km} km · {site.photos.length} photo{site.photos.length === 1 ? "" : "s"}</span>
      </div>
      {lk?.line_summary && <p className="site-summary">{lk.line_summary}</p>}
      {!lk && <p className="muted small">Gemini description: {site.look_source}</p>}
      <div className="site-photos">
        {site.photos.map((ph) => {
          const p = ph.point;
          const w = lk?.worst_photo === ph.n + 1;
          return (
            <figure key={ph.n} className={"site-photo" + (w ? " worst" : "")}>
              {ph.tile
                ? <img src={siteImageUrl(line, ph.n, ph.tile.item)} alt={`Aerial photo ${ph.n + 1} along ${line}`} />
                : <div className="missing muted small">no photo</div>}
              <figcaption>
                <b>Photo {ph.n + 1}</b>{w ? " · worst spot" : ""}<br />
                <span className="muted">
                  {p.zone}{p.landfire_fuel ? ` · ${p.landfire_fuel}` : ""}
                  {p.downwind ? ` · ~${p.downwind.people.toLocaleString()} downwind` : ""}
                  {p.downwind?.hospitals ? ` · ${p.downwind.hospitals} hospital${p.downwind.hospitals > 1 ? "s" : ""}` : ""}
                </span>
                {ph.look && (
                  <>
                    {ph.look.summary && <div>{ph.look.summary}</div>}
                    <div className="tags">
                      {ph.look.land_cover.map((x) => <span key={x} className="tag">{x}</span>)}
                      <span className="tag">buildings: {ph.look.structures}</span>
                      <span className="tag">vegetation: {ph.look.vegetation_near_tower}</span>
                      {ph.look.wildland_urban_interface && <span className="tag warn">wildland–urban edge</span>}
                      <span className="tag">access: {ph.look.access}</span>
                    </div>
                  </>
                )}
                {ph.tile && <div className="muted">NAIP {ph.tile.photo_date}</div>}
              </figcaption>
            </figure>
          );
        })}
      </div>
      {site.checks.length > 0 && (
        <div className="checks">
          {site.checks.map((c, k) => <div key={k} className="check">⚠ Photo {c.photo}: {c.message}</div>)}
        </div>
      )}
      {site.notes.length > 0 && <div className="muted small">{site.notes.join(" ")}</div>}
      <div className="muted small">{site.disclaimer}{lk ? ` Gemini confidence: ${lk.confidence}. Source: ${site.look_source}.` : ""}</div>
    </div>
  );
}
