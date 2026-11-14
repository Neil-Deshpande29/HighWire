import type { ReactNode } from "react";
import type { Brief, BriefLine } from "../api";

/** Situation brief: the one deterministic page of facts (physics, data, validation) that every co-pilot agent starts
 *  from. Built by code in backend/agents/brief.py; this panel only shows it. */

const num = (x: number | null | undefined, nd = 0, unit = "") =>
  x == null || Number.isNaN(x) ? "–" : `${x.toFixed(nd)}${unit}`;

function Section({ title, n, children }: { title: string; n: number; children: ReactNode }) {
  return (
    <details className="brief-sec">
      <summary>{title} <span className="muted">({n})</span></summary>
      {children}
    </details>
  );
}

function LineRows({ rows }: { rows: BriefLine[] }) {
  return (
    <ul className="brief-list">
      {rows.map((l) => (
        <li key={l.id}>
          <b>{l.id}</b>
          <span className="tags">
            <span className="tag">{num(l.kv, 0, " kV")}</span>
            {l.zone && <span className="tag">{l.zone}</span>}
            <span className="tag">risk {num(l.risk, 2)}</span>
            <span className="tag">gust {num(l.gust_ms, 1, " m/s")}</span>
            <span className="tag">RH {num(l.rh_pct, 0, "%")}</span>
            {l.people_downwind != null && <span className="tag">~{l.people_downwind.toLocaleString()} downwind</span>}
            {!!l.hospitals_downwind && <span className="tag warn">{l.hospitals_downwind} hospital{l.hospitals_downwind > 1 ? "s" : ""}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function BriefPanel({ brief }: { brief: Brief }) {
  const fire = brief.fire;
  const shut = fire?.shut_off_candidates ?? [];
  const patrol = fire?.patrol_corridors ?? [];
  const damage = brief.storm?.lines_damage_likely ?? [];
  const over = brief.grid?.overloaded ?? [];
  const crit = brief.grid?.critical_on_backup ?? [];
  const site = brief.site ?? [];
  const trust = brief.trust ?? [];
  const heads = brief.headlines ?? [];

  return (
    <div className="card brief">
      <div className="brief-head"><b>📋 Situation brief · shared by all agents</b></div>
      {heads.length > 0 && <ul className="brief-heads">{heads.map((h, k) => <li key={k}>{h}</li>)}</ul>}

      {shut.length > 0 && <Section title="Shut-off candidates" n={shut.length}><LineRows rows={shut} /></Section>}
      {patrol.length > 0 && <Section title="Patrol corridors" n={patrol.length}><LineRows rows={patrol} /></Section>}
      {damage.length > 0 && (
        <Section title="Storm damage likely" n={damage.length}>
          <ul className="brief-list">
            {damage.map((l) => (
              <li key={l.id}>
                <b>{l.id}</b>
                <span className="tags">
                  <span className="tag">{num(l.kv, 0, " kV")}</span>
                  {l.zone && <span className="tag">{l.zone}</span>}
                  <span className="tag">p(damage) {num(l.p_damage, 2)}</span>
                  <span className="tag">gust {num(l.gust_ms, 1, " m/s")}</span>
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {over.length > 0 && (
        <Section title="Overloaded lines" n={over.length}>
          <div className="tags">{over.map((o) => <span key={o.id} className="tag warn">{o.id} · {num(o.loading_pct, 0, "%")}</span>)}</div>
        </Section>
      )}
      {crit.length > 0 && (
        <Section title="Critical sites on backup" n={crit.length}>
          <div className="tags">{crit.map((c) => <span key={c} className="tag">{c}</span>)}</div>
        </Section>
      )}
      {site.length > 0 && (
        <Section title="Site agent (aerial photos)" n={site.length}>
          <ul className="brief-list">
            {site.map((s) => (
              <li key={s.line}>
                <b>{s.line}</b>{s.line_summary ? <>: {s.line_summary}</> : <span className="muted"> no description</span>}
                {(s.disagreements ?? []).map((d, k) => <div key={k} className="brief-warn">⚠ {d}</div>)}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {trust.length > 0 && (
        <Section title="How far to trust it" n={trust.length}>
          <ul className="brief-list">
            {trust.map((t) => (
              <li key={t.check}>
                <b>{t.check}</b> {t.status && <span className="tag">{t.status}</span>} {t.headline}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <div className="muted small">Built by code from physics, data and validation; no LLM numbers, no hindsight.</div>
    </div>
  );
}
