import { Link, useParams } from "react-router-dom";
import { ResultPanel } from "../components/ResultPanel";
import { Trace } from "../components/Trace";
import { when } from "../format";
import { useFetch } from "../hooks";
import type { TrialDetail, TrialView } from "../types";

function toView(t: TrialDetail): TrialView {
  return {
    trial_id: t.id,
    sweep_label: t.sweep_label,
    task_id: t.task_id,
    architecture_id: t.architecture_id,
    fault_config: t.fault_config,
    seed: t.seed,
    outcome: t.outcome,
    reason: t.reason,
    answer: t.answer,
    tool_calls: t.tool_calls,
    tool_errors: t.tool_errors ?? 0,
    retries: t.retries,
    llm_calls: t.llm_calls ?? 0,
    tokens_used: t.tokens_used,
    duration_ms: t.duration_ms ?? 0,
    fault_fired: !!t.fault_fired,
    checks: t.checks,
    world_diff: t.world_diff,
    feedback: t.feedback,
    attribution: t.attribution,
  };
}

export function TrialPage() {
  const { id = "" } = useParams();
  const { data: trial, error } = useFetch<TrialDetail>(`/api/trials/${id}`);

  if (error) {
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    );
  }
  if (!trial) return <div className="page" />;

  const fromPlayground = trial.sweep_label === "playground";
  const rerun =
    `/?task=${trial.task_id}&arch=${encodeURIComponent(trial.architecture_id)}&fault=${trial.fault_config}` +
    `&seed=${trial.seed}${trial.model ? `&model=${encodeURIComponent(trial.model)}` : ""}`;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="secondary" style={{ fontSize: 12.5, marginBottom: 2 }}>
            {fromPlayground ? (
              <Link to="/">Playground</Link>
            ) : (
              <>
                <Link to="/sweeps">Sweeps</Link> / <Link to={`/sweeps/${trial.sweep_label}`}>{trial.sweep_label}</Link>
              </>
            )}{" "}
            / trial
          </div>
          <h1 className="mono" style={{ fontSize: 16 }}>
            {trial.id.slice(0, 8)}
          </h1>
        </div>
        <div className="kv" style={{ alignSelf: "flex-end" }}>
          <span>
            task <b>{trial.task_id}</b>
          </span>
          <span>
            harness <b>{trial.architecture_id}</b>
            {trial.harness_fp && (
              <span className="muted mono" title="harness fingerprint" style={{ marginLeft: 6 }}>
                {trial.harness_fp}
              </span>
            )}
          </span>
          {trial.model && (
            <span>
              model <b>{trial.model}</b>
            </span>
          )}
          <span>
            faults <b>{trial.fault_config}</b>
          </span>
          {!fromPlayground && (
            <span>
              repeat <b>{trial.repeat_idx}</b>
            </span>
          )}
          <span>
            seed <b>{trial.seed}</b>
          </span>
          <span>
            recorded <b>{when(trial.created_at)}</b>
          </span>
        </div>
        <div className="actions">
          <Link className="button" to={rerun}>
            Re-run in playground
          </Link>
        </div>
      </div>
      <div className="split">
        <div className="panel">
          <div className="panel-head">
            <span className="label">Trace</span>
          </div>
          <Trace events={trial.events} />
        </div>
        <ResultPanel view={toView(trial)} running={false} />
      </div>
    </div>
  );
}
