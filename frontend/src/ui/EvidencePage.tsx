import { useEffect, useState } from "react";
import { API } from "../api";
import { la, resultUrl, type Card, type V5Case } from "../la/api";

// Every number on this page is read from validation/results/*.json. Nothing is typed in by hand.
type V5 = { cases: V5Case[]; median_model_top_pct_test: number; n_test_cases: number };
type V7 = { frac_high_risk_inside_red_flag: number; frac_red_flag_zone_hours_with_high_risk: number };
type V8 = {
  event_test: { by_lead: Record<string, { exploratory_fire_warning_to_main_surge_h: number | null; ignitions: { fire: string; model_top_pct: number }[] }> };
  year_2024: { auc_storm_day_ahead: number; auc_gust_day_ahead: number };
};
type V4b = { features: Record<string, { auc_report: number }> };

const WORDS: Record<string, string> = { pass: "Passed", mixed: "Mixed", weak: "Weak", fail: "Failed", "small-sample": "Promising · small sample" };
const pct = (x: number) => `${Math.max(1, Math.round(x))}%`;

function Claim({ title, sentence, big, bigLabel, how, status }: { title: string; sentence: string; big: string; bigLabel: string; how: string; status?: string }) {
  return (
    <div className="card claim">
      <div className="claim-top"><h3>{title}</h3>{status && <span className={`verdict v-${status}`}>{WORDS[status] ?? status}</span>}</div>
      <div className="claim-big"><b>{big}</b><span>{bigLabel}</span></div>
      <p>{sentence}</p>
      <p className="muted small">How we tested it: {how}</p>
    </div>
  );
}

export default function EvidencePage() {
  const [cards, setCards] = useState<Card[] | null>(null);
  const [v5, setV5] = useState<V5 | null>(null);
  const [v7, setV7] = useState<V7 | null>(null);
  const [v8, setV8] = useState<V8 | null>(null);
  const [v4b, setV4b] = useState<V4b | null>(null);
  const [zoom, setZoom] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    la.validation().then((v) => setCards(v.cards)).catch((e) => setErr(String(e)));
    la.result<V5>("v5_ignition.json").then(setV5).catch(() => null);
    la.result<V7>("v7_redflag.json").then(setV7).catch(() => null);
    la.result<V8>("v8_forecast.json").then(setV8).catch(() => null);
    la.result<V4b>("v4b_discrimination.json").then(setV4b).catch(() => null);
  }, []);
  const st = (id: string) => cards?.find((c) => c.id === id)?.status;
  const card = (id: string) => cards?.find((c) => c.id === id);
  if (err) return <div className="evidence"><div className="error">{err}</div></div>;

  const test = v5?.cases.filter((c) => c.split === "test") ?? [];
  const nLines = test[0]?.n_lines;
  const d1 = v8?.event_test.by_lead.lead_1d;
  const warn = d1?.exploratory_fire_warning_to_main_surge_h;
  return (
    <div className="evidence">
      <div className="ev-head">
        <h1>Does Highwire get it right?</h1>
        <p>We tuned the models on some storms and tested them on others we never tuned on. Everything below comes from those
          untouched test storms, and we show the losses along with the wins.</p>
      </div>
      <div className="claims">
        {v5 && <Claim title="Flags dangerous lines before fires start" status={st("V5")}
          big={`top ${pct(v5.median_model_top_pct_test)}`} bigLabel={`of ${nLines ?? "all"} lines (median)`}
          sentence={`For ${test.length} fires the models never saw, the power line nearest where each fire started ranked among the riskiest lines: ${test.map((c) => `${c.fire.replace(/ Fire$/, "")} top ${pct(c.model_top_pct)}`).join(", ")}.`}
          how="replayed the recorded weather and ranked every line at the hour each fire started. Only a few fires, so treat these as individual cases." />}
        {d1 && warn != null && <Claim title="Warns ahead of big outages" status={st("V8")}
          big={`${Math.round(warn)} h`} bigLabel="of warning from a day-old forecast"
          sentence={`Using only the weather forecast issued a day earlier, the fire alarm went off about ${Math.round(warn)} hours before outages in LA County reached half their peak in the January 2025 storm. The lines nearest the Eaton and Hurst fires ranked ${d1.ignitions.map((x) => `top ${pct(x.model_top_pct)}`).join(" and ")}.`}
          how="fed archived forecasts (what anyone could have known at the time) into the same models, with no retuning. One storm so far." />}
        {v7 && <Claim title="Agrees with the National Weather Service" status={st("V7")}
          big={pct(100 * v7.frac_high_risk_inside_red_flag)} bigLabel="of high-risk line-hours were inside a Red Flag Warning"
          sentence="When Highwire marks a line as high fire risk, forecasters at the National Weather Service had almost always issued a Red Flag Warning for that area, a judgement made independently of us."
          how="compared every flagged line-hour in the test storms with the official warning areas and times." />}
      </div>

      <h2>Where we fall short</h2>
      <ul className="short">
        {v8 && <li>Over all of 2024, just watching the forecast's strongest wind gust picked out the worst outage hours better than our storm-damage score
          ({v8.year_2024.auc_gust_day_ahead.toFixed(2)} vs {v8.year_2024.auc_storm_day_ahead.toFixed(2)}, where 0.5 is a coin flip and 1 is perfect).</li>}
        {v4b && <li>On recorded weather for 2022-2024, the storm-damage score only ties that same gust-only rule
          ({v4b.features.storm_index?.auc_report.toFixed(2)} vs {v4b.features.baseline_max_gust?.auc_report.toFixed(2)}).</li>}
        {v5 && <li>Only {v5.n_test_cases} test fires exist in our area and period, too few to call this a statistic.</li>}
        {card("V6") && <li>Matching the utility's actual shutoffs: {card("V6")!.headline}.</li>}
        <li>The grid starts at 66 kV (public map data). Most utility-caused fires start on smaller distribution lines we can't see, and line ratings are estimated from voltage class.</li>
      </ul>

      <details className="drawer full">
        <summary>Full results (V1-V8)</summary>
        <p className="muted">Each check, its baseline and its verdict. <a href={`${API}/report`} target="_blank" rel="noreferrer">Full report (REPORT.md)</a></p>
        <div className="v-grid">
          {cards?.map((c) => (
            <div key={c.id} className="card v-card">
              <div className="claim-top"><b>{c.id} · {c.title}</b><span className={`verdict v-${c.status}`}>{WORDS[c.status] ?? c.status}</span></div>
              <div className="v-headline">{c.headline}</div>
              <div className="muted small">Baseline: {c.baseline}</div>
              {c.plot && <img src={resultUrl(c.plot)} alt={c.id} onClick={() => setZoom(c.plot)} />}
              <div className="small">{c.one_liner}</div>
            </div>
          ))}
        </div>
      </details>
      {zoom && <div className="zoom" onClick={() => setZoom(null)}><img src={resultUrl(zoom)} alt="plot" /></div>}
    </div>
  );
}
