import { useState } from "react";
import type { LAStatic } from "../la/api";

/** Find a line by id ("L249") or by part of its name, select it and bring it into view. Below the box: what is known
 *  about whichever line is selected, however it was selected. */
export default function LineSearch({ s, selected, onFind }: { s: LAStatic; selected: string | null; onFind: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [miss, setMiss] = useState(false);
  const go = () => {
    const text = q.trim().toLowerCase();
    if (!text) return;
    const hit = s.branches.find((b) => b.id.toLowerCase() === text) ?? s.branches.find((b) => b.name?.toLowerCase().includes(text));
    setMiss(!hit);
    if (hit) onFind(hit.id);
  };
  return (
    <form className="line-search glass" onSubmit={(e) => { e.preventDefault(); go(); }}>
      <input value={q} onChange={(e) => { setQ(e.target.value); setMiss(false); }} placeholder="Find a line, e.g. L249" aria-label="Find a line by id or name" />
      {miss ? <span className="muted small">No line matches "{q.trim()}".</span> : selected && <span className="small">{selected}</span>}
    </form>
  );
}
