import { useEffect, useState } from "react";
import { API } from "../api";
import { la, resultUrl, type Card } from "./api";

const STATUS: Record<string, string> = { pass: "#22c55e", mixed: "#eab308", weak: "#f97316", fail: "#ef4444", "small-sample": "#a78bfa" };

export default function ValidationTab() {
  const [cards, setCards] = useState<Card[] | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { la.validation().then((v) => setCards(v.cards)).catch((e) => setErr(String(e))); }, []);
  if (err) return <div className="error pad">{err}</div>;
  if (!cards) return <div className="pad muted">Loading validation results…</div>;
  return (
    <div className="validation">
      <div className="v-head">
        <h2>Model vs history: what we tested and how it did</h2>
        <p className="muted">
          Every number is read from <code>validation/results/*.json</code>. Train/test split by event was fixed before any run. Failures are shown,
          not hidden. <a href={`${API}/report`} target="_blank" rel="noreferrer">Full report (REPORT.md)</a>
        </p>
      </div>
      <div className="v-grid">
        {cards.map((c) => (
          <div key={c.id} className="card v-card">
            <div className="v-top">
              <span className="pid">{c.id}</span>
              <span className="ptitle">{c.title}</span>
              <span className="chip" style={{ borderColor: STATUS[c.status] ?? "#94a3b8", color: STATUS[c.status] ?? "#94a3b8" }}>{c.status}</span>
            </div>
            <div className="v-headline">{c.headline}</div>
            <div className="v-base"><span className="muted">Baseline:</span> {c.baseline}</div>
            {c.plot && <img src={resultUrl(c.plot)} alt={c.id} onClick={() => setZoom(c.plot)} />}
            <div className="v-one">{c.one_liner}</div>
          </div>
        ))}
      </div>
      {zoom && <div className="zoom" onClick={() => setZoom(null)}><img src={resultUrl(zoom)} alt="plot" /></div>}
    </div>
  );
}
