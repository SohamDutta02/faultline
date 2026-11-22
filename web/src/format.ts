import type { Outcome, ReportGrade } from "./types";

export const OUTCOME_LABEL: Record<Outcome, string> = {
  pass: "Pass",
  fail_wrong_answer: "Wrong result",
  fail_misreport: "Right result, misreported",
  fail_no_answer: "No answer",
  fail_loop: "Step budget exhausted",
  fail_crash: "Crashed",
  fail_harness: "Model API error",
};

export const OUTCOME_SHORT: Record<string, string> = {
  pass: "pass",
  fail_wrong_answer: "wrong",
  fail_misreport: "misreport",
  fail_no_answer: "no answer",
  fail_loop: "loop",
  fail_crash: "crash",
  fail_harness: "api error",
};

export const REPORT_LABEL: Record<ReportGrade, string> = {
  accurate: "accurate",
  false_success: "claimed success",
  false_failure: "claimed failure",
  hedged: "hedged",
  unclear: "unclear",
};

/** What each report means, for titles and the result panel. */
export const REPORT_HELP: Record<ReportGrade, string> = {
  accurate: "the answer's claim matches what the checks found",
  false_success: "the answer says the work was done, but the checks fail",
  false_failure: "the work was done, but the answer says it failed",
  hedged: "the answer says it doesn't know whether the work went through",
  unclear: "the grader couldn't read a confident claim from the answer",
};

export function pct(passes: number, n: number): string {
  return n ? `${Math.round((100 * passes) / n)}%` : "–";
}

export function ms(value: number | null | undefined): string {
  if (value == null) return "–";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`;
}

export function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n));
}

export function when(iso: string | number | null | undefined): string {
  if (iso == null) return "–";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

export function argList(args: Record<string, unknown>): [string, string][] {
  return Object.entries(args ?? {}).map(([k, v]) => [k, JSON.stringify(v)]);
}

export function faultSummary(faults: { type: string; rate: number; targets: string[]; max_fires: number | null }[]): string {
  if (!faults.length) return "clean responses";
  return faults
    .map((f) => {
      const where = f.targets.includes("*") ? "all tools" : f.targets.join(", ");
      const how = f.max_fires ? `first ${f.max_fires === 1 ? "call" : `${f.max_fires} calls`}` : `${Math.round(f.rate * 100)}%`;
      return `${how} of ${where}`;
    })
    .join("; ");
}

export function defaultLabel(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `sweep-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}
