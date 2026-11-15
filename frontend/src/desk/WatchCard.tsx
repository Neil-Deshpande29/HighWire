import type { Watch } from "../la/api";

const WORDS: Record<Watch["level"], [string, string]> = {
  green: ["All clear", "Nothing is near a limit. No need to call the agents."],
  yellow: ["Keep an eye on it", "Something is building. Call the agents if you want a closer look."],
  red: ["Call the agents", "A hard check is at or near its limit, or heading there fast. It may be a small fix."],
};

/** The risk watch, always on screen: one light (green, yellow, flashing red), the score behind it, why, and the button
 *  that calls the agents. The score is a cheap equation over a few hard checks and their rate of change
 *  (backend/la/watch.py); it runs for every hour as the clock moves, with or without the agents. Red is early on
 *  purpose: it says "have the agents look", not "something is certainly wrong". */
export default function WatchCard({ watch, running, called, study, canCall, onCall }: {
  watch: Watch | undefined; running: boolean; called: boolean; study: boolean; canCall: boolean; onCall: () => void;
}) {
  const level = watch?.level ?? "green";
  const [head, hint] = WORDS[level];
  return (
    <section className={`card watch ${level}`} aria-live="polite" aria-label="Risk watch">
      <div className="watch-top">
        <span className={`watch-light ${level}`} role="img" aria-label={`Watch is ${level}`} />
        <div className="watch-text">
          <b>{watch ? head : "Reading the hour…"}</b>
          <span>{watch?.reasons[0] ?? hint}</span>
        </div>
        {watch && (
          <div className="watch-score" title={`Risk score: 100 means a hard limit is reached. Yellow from ${watch.yellow_from}, red from ${watch.red_from}. It counts where each check is heading, not only where it is.`}>
            <b>{watch.score}</b><span>risk score</span>
          </div>
        )}
      </div>
      {watch && watch.reasons.length > 1 && <p className="watch-more">{watch.reasons.slice(1).join(" ")}</p>}
      <button className={`primary watch-call ${level === "red" && !called && !running ? "urge" : ""}`} disabled={!canCall || running} onClick={onCall}>
        {running ? "The agents are working…" : study ? "Ask the agents about this what-if" : called ? "Call the agents again" : "Call the agents"}
      </button>
    </section>
  );
}
