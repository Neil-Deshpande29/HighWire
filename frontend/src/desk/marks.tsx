import type { Authority, Severity } from "../api";

/** Small marks shared by every desk panel. A level is always a shape, a word and a colour, never colour alone. */

const SEVERITY: Record<Severity, { mark: string; word: string }> = {
  high: { mark: "▲", word: "High" }, medium: { mark: "◆", word: "Medium" }, low: { mark: "●", word: "Low" },
};
export const sevMark = (level: Severity) => SEVERITY[level].mark;
export const sevWord = (level: Severity) => SEVERITY[level].word;

export function Sev({ level, word }: { level: Severity; word?: string }) {
  return <span className={`sev sev-${level}`}>{SEVERITY[level].mark} {word ?? SEVERITY[level].word}</span>;
}

/** What kind of thing a number rests on. Shown next to every source so stand-in or modelled data is never mistaken for a measurement. */
export type Kind = "Live" | "Recorded" | "Modelled" | "Stand-in" | "Missing";
const KIND_HELP: Record<Kind, string> = {
  Live: "Fetched from the source while you work",
  Recorded: "A record of what was measured or issued at the time",
  Modelled: "Computed by a model from other inputs, not measured",
  "Stand-in": "Public data standing in for a licensed source that is not connected",
  Missing: "Not available; the screen works without it",
};
export function KindBadge({ kind }: { kind: Kind }) {
  return <span className={`kind kind-${kind.toLowerCase()}`} title={KIND_HELP[kind]}>{kind}</span>;
}

const AUTHORITY: Record<Authority["level"], { mark: string; text: string }> = {
  operator: { mark: "◇", text: "Within operator authority" },
  supervisor: { mark: "↑", text: "Needs supervisor" },
  blocked: { mark: "✕", text: "Can't be approved" },
};
/** Who may approve this option. The reason and the rule behind it are in the tooltip and spelled out beside the buttons. */
export function AuthorityPill({ a }: { a?: Authority | null }) {
  if (!a) return <span className="auth auth-none" title="The backend did not say who may approve this">Authority not assessed</span>;
  return (
    <span className={`auth auth-${a.level}`} title={`${a.reasons.length ? `${a.reasons.join("; ")}. ` : ""}${a.basis}`}>
      {AUTHORITY[a.level].mark} {AUTHORITY[a.level].text}
    </span>
  );
}

/** Fixed honesty lines. Written once here so no panel rewords them. */
export const INDEX_NOTE = "Fire risk is a 0 to 1 index tuned on past outage spikes. It ranks lines; it is not the chance that a line starts a fire.";
export const FEEDER_NOTE = "Lines of 66 kV and up are modelled. \"Loses grid power\" means the substation serving the area is cut off, not that a feeder was traced to the building.";
export const SCORES_NOTE = "Two separate things, never added together: what acting costs, and what not acting leaves exposed.";
export const CONE_NOTE = "Downwind means inside a 10 km cone, 30 degrees either side of where the wind blows from the line.";
