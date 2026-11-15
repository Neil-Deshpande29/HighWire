import type { ReactNode } from "react";

/** One drop-down box in a dock. Closed, it is a single line: its name and the one fact worth knowing. Open, it shows
 *  the rest, and the summary line steps aside so no fact is on screen twice. Boxes stack, so nothing fights for the same space and the map stays visible. */
export default function Section({ title, summary, open, onToggle, onClose, main, tone, children }: {
  title: string; summary?: ReactNode; open: boolean; onToggle: () => void; onClose?: () => void; main?: boolean; tone?: "act" | "ok" | "study";
  children: ReactNode;
}) {
  return (
    <section className={`card sec ${open ? "open" : ""} ${main ? "main" : ""} ${tone ?? ""}`}>
      <div className="sec-bar">
        <button className="sec-head" aria-expanded={open} onClick={onToggle}>
          <span className="sec-title">{title}</span>
          <span className="sec-sum">{open ? null : summary}</span>
          <span className="sec-chev" aria-hidden="true">⌄</span>
        </button>
        {onClose && <button className="x sec-close" onClick={onClose} aria-label={`Close ${title}`}>×</button>}
      </div>
      {open && <div className="sec-body">{children}</div>}
    </section>
  );
}
