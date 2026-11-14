import { useEffect, useState } from "react";
import { la, siteImageUrl, type Site } from "./api";

/** What is on the ground along the selected line: aerial photos of its most fire-prone stretches, how much vegetation
 *  is near the wires, the data facts per photo, and (when an AI key is set) the site agent's description with every
 *  place it disagrees with the data. Context only; it changes no number. */
export default function SitePanel({ line, eid, i, onClose }: { line: string; eid: string; i: number; onClose: () => void }) {
  const [site, setSite] = useState<Site | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setErr(null);
    la.site(line, eid, i)
      .then((s) => { if (live) setSite(s); })
      .catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [line, eid, i]);

  const mine = site && site.line === line ? site : null;
  const f = mine?.facts, lk = mine?.look, veg = mine?.greenery;
  const dated = mine?.photos.find((p) => p.tile)?.tile;
  return (
    <div className="site glass">
      <div className="site-head">
        <b>{line} · aerial photos</b>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </div>
      {err && <div className="error small">Could not load this line's photos: {err}</div>}
      {!mine && !err && <div className="muted small">Fetching photos along {line}… the first look at a line takes a few seconds.</div>}
      {mine && f && (
        <>
          <div className="muted small">{Math.round(f.kv)} kV · {f.zone} · {f.length_km} km · {mine.photos.filter((p) => p.tile).length} of {mine.photos.length} photos</div>
          {veg && (
            <div className={`site-veg ${veg.level}`}>
              Vegetation near the wires: <b>{veg.level}</b> ({Math.round(veg.share * 100)}% of the ground within {veg.corridor_m} m)
              <span className="muted"> · {veg.method} method</span>
            </div>
          )}
          {lk?.line_summary && <p className="site-summary">{lk.line_summary}</p>}
          <div className="site-photos">
            {mine.photos.map((ph) => {
              const p = ph.point;
              const worst = lk?.worst_photo === ph.n + 1;
              return (
                <figure key={ph.n} className={`site-photo ${worst ? "worst" : ""}`}>
                  {ph.tile
                    ? <img src={siteImageUrl(line, ph.n, ph.tile.item)} alt={`Aerial photo ${ph.n + 1} along ${line}`} loading="lazy" />
                    : <div className="missing muted small">no photo</div>}
                  <figcaption>
                    <b>Photo {ph.n + 1}</b>{worst ? " · worst spot" : ""}
                    <span className="muted"> {p.zone}{p.landfire_fuel ? ` · ${p.landfire_fuel}` : ""}{p.downwind ? ` · ${p.downwind.people.toLocaleString()} people downwind` : ""}</span>
                    {ph.look && (
                      <>
                        {ph.look.summary && <div>{ph.look.summary}</div>}
                        <div className="tags">
                          {ph.look.land_cover.map((x) => <span key={x} className="tag">{x}</span>)}
                          <span className="tag">buildings: {ph.look.structures}</span>
                          <span className="tag">access: {ph.look.access}</span>
                          {ph.look.wildland_urban_interface && <span className="tag warn">homes meet brush</span>}
                        </div>
                      </>
                    )}
                  </figcaption>
                </figure>
              );
            })}
          </div>
          {mine.checks.map((c, k) => <div key={k} className="site-check">Check photo {c.photo}: {c.message}</div>)}
          {mine.notes.length > 0 && <div className="muted small">{mine.notes.join(" ")}</div>}
          <div className="muted small">
            {dated ? `Photos: ${dated.source}, ${dated.photo_date}. ` : ""}
            {lk ? `AI description (confidence ${lk.confidence}); it is context and changes no number.` : "No AI description of these photos: no key is set."}
          </div>
        </>
      )}
    </div>
  );
}
