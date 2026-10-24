export type Outcome =
  | "pass"
  | "fail_wrong_answer"
  | "fail_no_answer"
  | "fail_loop"
  | "fail_crash"
  | "fail_harness";

export type Slot = "instructions" | "tools" | "environment" | "state" | "feedback";
export type HarnessSpec = Record<Slot, string>;

export interface Architecture {
  id: string;
  description: string;
  needs_llm: boolean;
  retries: boolean;
  spec: HarnessSpec;
  /** the slots that can be edited: all five for a model harness, tools and environment for a script */
  slots: Slot[];
  fingerprint: string;
}

export interface HarnessSlot {
  id: Slot;
  description: string;
  baseline: string;
  options: { id: string; description: string }[];
}

export interface TaskInfo {
  id: string;
  prompt: string;
  category: string;
  seed: number;
  checks: string[];
  setup: { customer_id: string; sku: string; qty: number }[];
}

export interface FaultSpec {
  type: string;
  layer: string;
  phase: string;
  rate: number;
  targets: string[];
  max_fires: number | null;
  params: Record<string, unknown>;
}

export interface FaultConfig {
  name: string;
  faults: FaultSpec[];
}

export interface Meta {
  model: string;
  llm_available: boolean;
  architectures: Architecture[];
  tasks: TaskInfo[];
  fault_configs: FaultConfig[];
  fault_types: Record<string, { layer: string; phase: string; description: string }>;
  sweep_defaults: { configs: string[]; archs: string[]; tasks: string[] };
  harness: { slots: HarnessSlot[]; presets: string[] };
}

export interface TraceEvent {
  step_idx: number;
  kind: string;
  tool_name: string | null;
  fault_injected: boolean;
  latency_ms: number;
  payload: any;
  /** which client made a tool call: the agent, feedback through the proxy, or feedback reading ground truth */
  channel?: "agent" | "review" | "truth" | null;
}

export interface Attribution {
  cause: string;
  subsystem: Slot | null;
  evidence: string;
  also: string[];
  notes: string[];
}

export interface FeedbackRecord {
  option: string;
  first_pass: { outcome: Outcome; reason?: string; tool_calls: number; tokens: number };
  reviews: { round: number; verdict: string }[];
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Order {
  order_id: string;
  customer_id: string;
  sku: string;
  qty: number;
  unit_price: number;
  total: number;
  status: string;
}

export interface WorldDiff {
  inventory: { sku: string; name: string; before: number; after: number }[];
  orders_added: Order[];
  orders_changed: { order_id: string; customer_id: string; sku: string; qty: number; before: string; after: string }[];
}

/** A trial as the result panel and trace need it, live or stored. */
export interface TrialView {
  trial_id?: string;
  sweep_label?: string | null;
  task_id: string;
  architecture_id: string;
  fault_config: string;
  seed: number;
  outcome: Outcome;
  reason: string;
  answer: string | null;
  tool_calls: number;
  tool_errors: number;
  retries: number;
  llm_calls: number;
  tokens_used: number;
  duration_ms: number;
  fault_fired: boolean;
  checks: Check[];
  world_diff: WorldDiff | null;
  feedback?: FeedbackRecord | null;
  attribution?: Attribution | null;
}

export interface TrialRow {
  id: string;
  task_id: string;
  architecture_id: string;
  fault_config: string;
  repeat_idx: number;
  seed: number;
  outcome: Outcome;
  reason: string;
  fault_fired: number;
  tool_calls: number;
  tool_errors: number | null;
  retries: number;
  llm_calls: number | null;
  tokens_used: number;
  duration_ms: number | null;
  answer: string | null;
  attribution?: Attribution | null;
  created_at: string;
}

export interface TrialDetail extends TrialRow {
  sweep_label: string | null;
  checks: Check[];
  world_diff: WorldDiff | null;
  events: TraceEvent[];
  feedback: FeedbackRecord | null;
  harness_fp?: string | null;
}

export interface JobSummary {
  id: string;
  kind: "trial" | "sweep";
  title: string;
  params: Record<string, any>;
  status: "running" | "done" | "failed" | "stopped" | "paused";
  created_at: number;
  finished_at: number | null;
  error: string | null;
  progress: { done: number; total: number; planned: number } | null;
  result: any;
}

export interface JobEvent {
  seq: number;
  type: "start" | "event" | "result" | "error" | "end" | "plan" | "progress";
  t: number;
  [key: string]: any;
}

export interface SweepRow {
  id: string;
  label: string;
  model_id: string;
  repeats_per_cell: number;
  started_at: string;
  status: string;
  params: { archs: string[]; configs: string[]; tasks: string[]; repeats: number } | null;
  trials: number;
  passes: number | null;
  harness: number | null;
  last_trial_at: string | null;
  planned: number | null;
  live: boolean;
}

export interface Tally {
  passes: number;
  n: number;
}

export interface SweepSummary {
  label: string;
  model: string;
  status: string;
  started_at: string;
  repeats: number;
  graded: number;
  harness: number;
  archs: string[];
  configs: string[];
  tasks: string[];
  cells: (Tally & { config: string; arch: string })[];
  fired: { config: string; fired: number; n: number }[];
  outcomes: ({ arch: string } & Record<string, number>)[];
  cost: {
    arch: string;
    tool_calls: number;
    tool_errors: number;
    retries: number;
    llm_calls: number;
    tokens_used: number;
    duration_ms: number;
  }[];
  by_task: (Tally & { arch: string; task: string; config: string })[];
  params: SweepRow["params"];
  live_job: JobSummary | null;
  feedback: FeedbackStats[];
  spread: Spread[];
  comparisons: Comparison[];
  interactions: Interaction[];
  attribution: { arch: string; failures: number; causes: Record<string, number>; subsystems: Record<string, number> }[];
  versions: VersionInfo[];
}

export interface FeedbackStats {
  arch: string;
  n: number;
  first_pass: number;
  final: number;
  fixed: number;
  broke: number;
  flagged: number;
  false_alarms: number;
  missed: number;
  extra_tool_calls: number;
  extra_tokens: number;
}

export interface Spread {
  config: string;
  best: string;
  best_rate: number;
  worst: string;
  worst_rate: number;
  spread: number;
  harnesses: number;
}

export interface Comparison {
  slot: Slot;
  from_option: string;
  to_option: string;
  from_arch: string;
  to_arch: string;
  n: number;
  delta: number;
  d_tokens: number;
  d_tool_calls: number;
  d_llm_calls: number;
  by_config: Record<string, { delta: number; n: number }>;
}

export interface Interaction {
  slot: Slot;
  from_option: string;
  to_option: string;
  config: string;
  gap: number;
  contexts: { from_arch: string; to_arch: string; delta: number }[];
}

export interface VersionInfo {
  arch: string;
  current: string | null;
  recorded: Record<string, number>;
  versions: number;
  stale: number;
  unknown: number;
  changed: string[];
}

export interface AuditFinding {
  level: "error" | "warning" | "info";
  check: string;
  message: string;
}

export interface CompareResult {
  base: { label: string; model: string };
  head: { label: string; model: string };
  threshold: number;
  archs: {
    arch: string;
    pairs: number;
    base_rate: number;
    head_rate: number;
    delta: number;
    base_fp: string | null;
    head_fp: string | null;
    harness: "same" | "changed" | "unknown";
    changed: string[];
  }[];
  cells: CompareCell[];
  regressions: CompareCell[];
  improvements: CompareCell[];
  only_base: string[];
  only_head: string[];
}

export interface CompareCell {
  arch: string;
  config: string;
  pairs: number;
  base_rate: number;
  head_rate: number;
  delta: number;
}
