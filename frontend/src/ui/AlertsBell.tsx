import { useCallback, useEffect, useRef, useState } from "react";
import { la, type Alerts } from "../la/api";

const SOURCE: Record<string, string> = { code: "sent by code", agent: "sent by the orchestrator", test: "test" };

/** The bell in the top bar: every text alert that went out, or (when Twilio is not fully set up) that would have, who
 *  sent it, a test button and the on/off switch. See backend/notify.py. */
export default function AlertsBell() {
  const [data, setData] = useState<Alerts | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const seen = useRef(0);
  const [fresh, setFresh] = useState(0);
  const load = useCallback(() => {
    la.alerts().then((a) => {
      setData(a);
      const top = a.alerts[0]?.n ?? 0;
      if (top < seen.current) seen.current = 0; // the session was reset
      setFresh(a.alerts.filter((x) => x.n > seen.current).length);
    }).catch(() => null);
  }, []);
  useEffect(() => { load(); const h = window.setInterval(load, 4000); return () => window.clearInterval(h); }, [load]);
  useEffect(() => { if (open && data) { seen.current = data.alerts[0]?.n ?? 0; setFresh(0); } }, [open, data]);
  const act = async (call: () => Promise<Alerts>) => { setBusy(true); try { setData(await call()); } catch { /* shown as unchanged */ } finally { setBusy(false); } };
  const n = data?.alerts.length ?? 0;
  return (
    <div className="bell">
      <button className={`bar-btn bell-btn ${fresh ? "fresh" : ""}`} aria-expanded={open} aria-label={`Text alerts: ${n}${fresh ? `, ${fresh} new` : ""}`} onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true">🔔</span> {n}{data && !data.on ? " · off" : ""}
      </button>
      {open && data && (
        <div className="bell-pop glass" role="dialog" aria-label="Text alerts">
          <div className="bell-head">
            <b>Text alerts</b>
            <label className="switch"><input type="checkbox" checked={data.on} disabled={busy} onChange={(e) => act(() => la.alertsSwitch(e.target.checked))} /> {data.on ? "On" : "Off"}</label>
          </div>
          <p className="muted small">
            {data.dry_run
              ? <>Dry run: nothing is sent. Missing in <code>.env</code>: {data.missing.join(", ")}.</>
              : <>Texts go to the {data.to}.</>}
          </p>
          {n === 0 && <p className="muted">No alerts yet. One goes out when a line is at very high fire risk, is likely to be knocked down, or a plan that turns lines off is approved.</p>}
          <ul className="bell-list">
            {data.alerts.map((a) => (
              <li key={a.n}>
                <span className={`bell-status ${a.status.replace(" ", "-")}`}>{a.status === "dry run" ? "would have sent" : a.status}</span>
                <span className="muted small"> · {SOURCE[a.source] ?? a.source}</span>
                <div>{a.message}</div>
              </li>
            ))}
          </ul>
          <button className="secondary small-btn" disabled={busy || !data.on} onClick={() => act(la.alertsTest)}>Send test text</button>
        </div>
      )}
    </div>
  );
}
