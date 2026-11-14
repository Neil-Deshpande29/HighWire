import { useEffect, useState } from "react";
import type { Role } from "../la/api";
import type { Option } from "./OptionsTable";

/** Why a plan was rejected: the fixed choices the coordinator understands (backend/la/feedback.py). */
export const REJECT_REASONS: [string, string][] = [
  ["too_many_customers", "Cuts off too many customers"],
  ["critical_site", "Puts a hospital or water plant on backup"],
  ["not_enough", "Leaves too much fire risk"],
  ["wrong_lines", "Wrong lines"],
];

/** The bar that never scrolls away: Approve or Reject the selected plan.
 *  Reject asks why, and the answer goes to the coordinator for the next run.
 *  A plan over the operator's limit needs a second, supervisor sign-off; the backend refuses it otherwise. */
export default function DecisionBar({ option, study, canAct, note, nRecord, busy, onApprove, onReject, onRecord }: {
  option: Option | null; study: boolean; canAct: boolean; note?: string; nRecord: number; busy: boolean;
  onApprove: (role: Role) => void; onReject: (reasons: string[], note: string) => void; onRecord: () => void;
}) {
  const [step, setStep] = useState<"idle" | "signoff" | "reject">("idle");
  const [reasons, setReasons] = useState<string[]>([]);
  const [text, setText] = useState("");
  useEffect(() => { setStep("idle"); setReasons([]); setText(""); }, [option?.key]);

  const plan = option?.kind === "plan" ? option.plan : undefined;
  const auth = plan?.authority ?? option?.sim?.authority ?? null;
  const safe = option?.sim?.is_safe ?? plan?.result.is_safe ?? false;
  const status = option?.status;
  let body;
  if (study) body = <span className="muted">Study mode: nothing here can be approved.</span>;
  else if (!option) body = <span className="muted">Pick an option above to approve or reject it.</span>;
  else if (!plan) body = <span className="muted"><b>{option.label}</b> is shown for comparison.</span>;
  else if (status === "approved" || status === "rejected" || status === "superseded") {
    body = <span><b>{option.label}</b> · <span className={`status as-is st-${status}`}>{status === "superseded" ? "Replaced by the approved plan" : status === "approved" ? "Approved" : "Rejected · your reason went to the coordinator"}</span></span>;
  } else if (step === "reject") {
    body = (
      <div className="bar-form">
        <b>Why reject {option.label}? The coordinator uses your answer next time.</b>
        <div className="chips">
          {REJECT_REASONS.map(([id, label]) => (
            <button key={id} className={`chip-btn ${reasons.includes(id) ? "on" : ""}`} aria-pressed={reasons.includes(id)}
              onClick={() => setReasons((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]))}>{label}</button>
          ))}
        </div>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={400} placeholder="Anything else? (optional)" aria-label="Note for the coordinator" />
        <div className="bar-btns">
          <button className="primary" disabled={busy || (!reasons.length && !text.trim())} onClick={() => onReject(reasons, text.trim())}>Reject and send</button>
          <button className="secondary" onClick={() => setStep("idle")}>Cancel</button>
        </div>
      </div>
    );
  } else if (step === "signoff") {
    body = (
      <div className="bar-form">
        <b>This is a big cut, so it needs a supervisor.</b>
        <span className="bar-why">{(auth?.reasons ?? []).map((r) => r.charAt(0).toUpperCase() + r.slice(1)).join(". ")}.</span>
        <div className="bar-btns">
          <button className="primary" disabled={busy} onClick={() => onApprove("supervisor")}>Approve as supervisor</button>
          <button className="secondary" onClick={() => setStep("idle")}>Cancel</button>
        </div>
      </div>
    );
  } else {
    body = (
      <>
        <div className="bar-line">
          <b>{option.label}</b>
          <span className={safe ? "pass" : "fail"}>{safe ? "✓ Safe" : "✕ Not safe"}</span>
          {safe && auth?.level === "supervisor" && <span className="auth auth-supervisor">Needs supervisor</span>}
        </div>
        <div className="bar-btns">
          {safe && <button className="primary" disabled={!canAct || busy} onClick={() => (auth?.level === "supervisor" ? setStep("signoff") : onApprove("operator"))}>Approve</button>}
          <button className="secondary" disabled={busy} onClick={() => setStep("reject")}>Reject</button>
          {note && <span className="muted small">{note}</span>}
        </div>
      </>
    );
  }
  return (
    <div className={`decision-bar ${study ? "study" : ""}`}>
      <div className="bar-main">{body}</div>
      <button className="link small" onClick={onRecord}>Log ({nRecord})</button>
    </div>
  );
}
