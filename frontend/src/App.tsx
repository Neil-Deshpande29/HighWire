import { useState } from "react";
import LAApp from "./la/LAApp";
import ValidationTab from "./la/ValidationTab";
import WhatIfApp from "./WhatIfApp";

const TABS = [
  { id: "replay", label: "LA Replay" },
  { id: "validation", label: "Validation" },
  { id: "whatif", label: "What-if (synthetic)" },
] as const;

export default function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("replay");
  return (
    <div className="shell">
      <nav className="tabs">
        <span className="brand">⚡ Highwire</span>
        {TABS.map((t) => <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>{t.label}</button>)}
      </nav>
      <div className="tabbody">
        {tab === "replay" && <LAApp />}
        {tab === "validation" && <ValidationTab />}
        {tab === "whatif" && <WhatIfApp />}
      </div>
    </div>
  );
}
