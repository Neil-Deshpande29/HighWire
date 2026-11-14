import { useCallback, useEffect, useState } from "react";
import LAApp from "./la/LAApp";
import { la, type LAEvent, type LAStatic } from "./la/api";
import EvidencePage from "./ui/EvidencePage";
import TopBar, { type Mode } from "./ui/TopBar";

/** One screen: the grid at one moment (live forecast "Now" or a past event), Operate or Study, plus the Evidence page. */
export default function App() {
  const [s, setS] = useState<LAStatic | null>(null);
  const [events, setEvents] = useState<LAEvent[]>([]);
  const [eid, setEid] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("operate");
  const [page, setPage] = useState<"map" | "evidence">("map");
  const [t, setT] = useState<string | null>(null);
  const [fcBusy, setFcBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([la.static(), la.events()]).then(([st, ev]) => {
      setS(st); setEvents(ev);
      const linked = new URLSearchParams(window.location.search).get("event"); // a shared link names its event
      setEid(linked && ev.some((e) => e.id === linked && e.has_replay) ? linked
        : ev.some((e) => e.id === "FX") ? "FX" : ev.find((e) => e.kind === "wind" && e.has_replay)?.id ?? ev[0]?.id ?? null);
    }).catch(() => setErr("Can't reach the Highwire backend on :8000. Start it with ./run.sh"));
  }, []);

  // The live forecast is fetched from Open-Meteo; without network the last saved forecast stays as "Now".
  const refresh = useCallback(async () => {
    setFcBusy(true); setErr(null);
    try {
      await la.refreshForecast();
      for (let k = 0; k < 120; k++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await la.forecastStatus();
        if (st.status === "error") throw new Error(st.error ?? "forecast refresh failed");
        if (st.status === "done") break;
      }
      setEvents(await la.events()); setEid("FX"); setVersion((v) => v + 1);
    } catch (e) { setErr(`Couldn't refresh the forecast (showing the last saved one): ${e}`); } finally { setFcBusy(false); }
  }, []);

  if (!s || !eid) return <div className="loading">{err ?? "Loading the Los Angeles grid…"}</div>;
  return (
    <div className={`shell ${mode === "study" ? "study" : ""}`}>
      <TopBar events={events} eid={eid} t={t} mode={mode} page={page} fcBusy={fcBusy}
        onGoTo={(id) => { setEid(id); setMode("operate"); }} onMode={setMode} onRefresh={refresh} onPage={setPage} />
      {err && <div className="toast error" onClick={() => setErr(null)}>{err}</div>}
      <div style={{ display: page === "map" ? "contents" : "none" }}>
        <LAApp s={s} events={events} eid={eid} mode={mode} version={version} onMode={setMode} onTime={setT} />
      </div>
      {page === "evidence" && <EvidencePage />}
    </div>
  );
}
