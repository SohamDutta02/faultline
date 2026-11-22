import { OUTCOME_LABEL, OUTCOME_SHORT, REPORT_HELP, REPORT_LABEL } from "../format";
import type { Outcome, ReportGrade } from "../types";
import { Alert, Check, Cross, Fault, Loop, Minus } from "./Icons";

const TONE: Record<Outcome, string> = {
  pass: "good",
  fail_wrong_answer: "critical",
  fail_misreport: "serious",
  fail_no_answer: "serious",
  fail_loop: "serious",
  fail_crash: "critical",
  fail_harness: "neutral",
};

function OutcomeIcon({ outcome, size }: { outcome: Outcome; size?: number }) {
  if (outcome === "pass") return <Check size={size} />;
  if (outcome === "fail_loop") return <Loop size={size} />;
  if (outcome === "fail_harness") return <Minus size={size} />;
  if (outcome === "fail_crash") return <Alert size={size} />;
  return <Cross size={size} />;
}

/** Outcome as icon + text; color never carries it alone. */
export function OutcomeStatus({ outcome, short = false }: { outcome: Outcome; short?: boolean }) {
  return (
    <span className={`status ${TONE[outcome]}`}>
      <OutcomeIcon outcome={outcome} />
      {short ? OUTCOME_SHORT[outcome] : OUTCOME_LABEL[outcome]}
    </span>
  );
}

export function OutcomeHeadline({ outcome }: { outcome: Outcome }) {
  return (
    <div className={`outcome status ${TONE[outcome]}`}>
      <OutcomeIcon outcome={outcome} size={18} />
      {OUTCOME_LABEL[outcome]}
    </div>
  );
}

const REPORT_TONE: Record<ReportGrade, string> = {
  accurate: "good",
  false_success: "critical",
  false_failure: "serious",
  hedged: "neutral",
  unclear: "neutral",
};

/** The answer's report as icon + text, with what it means on hover. */
export function ReportStatus({ report }: { report: ReportGrade }) {
  const bad = report === "false_success" || report === "false_failure";
  return (
    <span className={`status ${REPORT_TONE[report]}`} title={REPORT_HELP[report]}>
      {report === "accurate" ? <Check /> : bad ? <Cross /> : <Minus />}
      {REPORT_LABEL[report]}
    </span>
  );
}

export function CheckIcon({ passed }: { passed: boolean }) {
  return (
    <span className={`status ${passed ? "good" : "critical"}`} title={passed ? "passed" : "failed"}>
      {passed ? <Check /> : <Cross />}
    </span>
  );
}

export function FaultChip({ fault, detail }: { fault: string; detail?: string }) {
  return (
    <span className="fault-chip" title={detail}>
      <Fault size={12} />
      {fault}
    </span>
  );
}

export function HttpStatus({ status }: { status: number | null }) {
  if (status == null) return <span className="http err">no response</span>;
  return <span className={`http${status >= 400 ? " err" : ""}`}>{status}</span>;
}

export function JobStatus({ status }: { status: string }) {
  const tone =
    status === "done" ? "good" : status === "failed" ? "critical" : status === "running" ? "neutral" : "serious";
  return (
    <span className={`status ${tone}`}>
      {status === "running" ? <span className="spinner" /> : status === "done" ? <Check /> : <Minus />}
      {status}
    </span>
  );
}
