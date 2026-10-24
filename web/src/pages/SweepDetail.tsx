import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { JobStatus, OutcomeStatus } from "../components/Badges";
import { AttributionPanel, ComparisonsPanel, FeedbackPanel, SpreadPanel, VersionNotes } from "../components/HarnessAnalysis";
import { Heatmap, HeatmapLegend } from "../components/Heatmap";
import { Fault, Stop } from "../components/Icons";
import { OUTCOME_SHORT, compact, ms, pct, when } from "../format";
import { CAUSE_LABEL } from "../harness";
import { useFetch } from "../hooks";
import type { SweepSummary, Tally, TrialRow } from "../types";

const OUTCOMES = ["pass", "fail_wrong_answer", "fail_no_answer", "fail_loop", "fail_crash"];

function Progress({ summary }: { summary: SweepSummary }) {
  const job = summary.live_job;
  if (!job?.progress) return null;
  const { done, total } = job.progress;
  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-body" style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span className="spinner" />
        <span className="num">
          {done} of {total} trials
        </span>
        <div className="meter" style={{ flex: 1 }}>
          <span style={{ width: `${total ? (100 * done) / total : 0}%` }} />
        </div>
        <button className="small" onClick={() => api.post(`/api/jobs/${job.id}/stop`)}>
          <Stop size={10} /> Stop
        </button>
      </div>
    </div>
  );
}

export function SweepDetail() {
  const { label = "" } = useParams();
  const navigate = useNavigate();
  const [poll, setPoll] = useState<number | undefined>(2500);
  const { data: s, error } = useFetch<SweepSummary>(`/api/sweeps/${encodeURIComponent(label)}`, poll);
  const live = !!s?.live_job;
  useEffect(() => {
    if (s) setPoll(live ? 2500 : undefined);
  }, [s, live]);

  const [selected, setSelected] = useState<[string, string] | null>(null);
  const [taskArch, setTaskArch] = useState<string | null>(null);
  const [filters, setFilters] = useState({ outcome: "", task: "" });
  const [shown, setShown] = useState(50);

  const query = new URLSearchParams();
  if (selected) {
    query.set("config", selected[0]);
    query.set("arch", selected[1]);
  }
  if (filters.outcome) query.set("outcome", filters.outcome);
  if (filters.task) query.set("task", filters.task);
  const trials = useFetch<TrialRow[]>(`/api/sweeps/${encodeURIComponent(label)}/trials?${query}`, live ? 2500 : undefined);

  const grid = useMemo(() => {
    const m = new Map<string, Tally>();
    s?.cells.forEach((c) => m.set(`${c.config}|${c.arch}`, c));
    return m;
  }, [s]);
  const taskGrid = useMemo(() => {
    const m = new Map<string, Tally>();
    s?.by_task.forEach((c) => m.set(`${c.arch}|${c.task}|${c.config}`, c));
    return m;
  }, [s]);

  if (error) {
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    );
  }
  if (!s) return <div className="page" />;

  const arch = taskArch ?? s.archs[0];
  const rate = (t?: Tally) => (t && t.n ? t.passes / t.n : null);
  const delta = (config: string, a: string) => {
    if (config === "none") return null;
    const base = rate(grid.get(`none|${a}`));
    const cur = rate(grid.get(`${config}|${a}`));
    if (base === null || cur === null) return null;
    const d = Math.round(100 * (cur - base));
    return `${d > 0 ? "+" : ""}${d} pts vs no faults`;
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="secondary" style={{ fontSize: 12.5, marginBottom: 2 }}>
            <Link to="/sweeps">Sweeps</Link> /
          </div>
          <h1 className="mono">{s.label}</h1>
        </div>
        <div className="kv" style={{ alignSelf: "flex-end" }}>
          <JobStatus status={live ? "running" : s.status} />
          <span>
            model <b>{s.model}</b>
          </span>
          <span>
            repeats <b>{s.repeats}</b>
          </span>
          <span>
            graded <b>{s.graded}</b>
          </span>
          {s.harness > 0 && (
            <span title="model API failures, excluded and re-run on resume">
              api errors <b>{s.harness}</b>
            </span>
          )}
          <span>
            started <b>{when(s.started_at)}</b>
          </span>
        </div>
      </div>

      <Progress summary={s} />
      <VersionNotes s={s} />

      {s.graded === 0 ? (
        <div className="panel">
          <div className="empty">No graded trials yet.</div>
        </div>
      ) : (
        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="label">Pass rate by fault config</span>
              <span className="spacer" />
              <HeatmapLegend />
            </div>
            <div className="panel-body">
              <Heatmap
                rows={s.configs}
                cols={s.archs}
                rowHeader="fault config"
                cell={(r, c) => grid.get(`${r}|${c}`)}
                note={delta}
                selected={selected}
                onSelect={(r, c) => setSelected(selected && selected[0] === r && selected[1] === c ? null : [r, c])}
              />
              <p className="secondary" style={{ fontSize: 12, marginTop: 10 }}>
                Select a cell to list its trials below. Architectures in a cell faced the same fault schedule.
              </p>
            </div>
          </div>

          <SpreadPanel s={s} />
          <ComparisonsPanel s={s} />
          <AttributionPanel s={s} />
          <FeedbackPanel s={s} />

          <div className="grid-2">
            <div className="panel">
              <div className="panel-head">
                <span className="label">Outcomes</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>architecture</th>
                      {OUTCOMES.map((o) => (
                        <th key={o} className="r">
                          {OUTCOME_SHORT[o]}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {s.outcomes.map((o) => (
                      <tr key={o.arch}>
                        <td className="mono">{o.arch}</td>
                        {OUTCOMES.map((k) => (
                          <td key={k} className="r num">
                            {o[k] || <span className="muted">0</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <span className="label">Faults fired</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>fault config</th>
                      <th className="r">trials hit</th>
                      <th style={{ width: "40%" }} />
                    </tr>
                  </thead>
                  <tbody>
                    {s.fired.map((f) => (
                      <tr key={f.config}>
                        <td className="mono">{f.config}</td>
                        <td className="r num">
                          {pct(f.fired, f.n)} <span className="muted">of {f.n}</span>
                        </td>
                        <td style={{ verticalAlign: "middle" }}>
                          {f.config !== "none" && f.fired === 0 ? (
                            <span className="status serious" style={{ fontSize: 12 }}>
                              <Fault size={12} /> never fired
                            </span>
                          ) : (
                            <div className="meter">
                              <span style={{ width: `${f.n ? (100 * f.fired) / f.n : 0}%` }} />
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Mean cost per trial</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>architecture</th>
                    <th className="r">tool calls</th>
                    <th className="r">tool errors</th>
                    <th className="r">client retries</th>
                    <th className="r">model calls</th>
                    <th className="r">tokens</th>
                    <th className="r">duration</th>
                  </tr>
                </thead>
                <tbody>
                  {s.cost.map((c) => (
                    <tr key={c.arch}>
                      <td className="mono">{c.arch}</td>
                      <td className="r num">{c.tool_calls.toFixed(1)}</td>
                      <td className="r num">{c.tool_errors.toFixed(1)}</td>
                      <td className="r num">{c.retries.toFixed(1)}</td>
                      <td className="r num">{c.llm_calls.toFixed(1)}</td>
                      <td className="r num">{compact(c.tokens_used)}</td>
                      <td className="r num">{ms(Math.round(c.duration_ms))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Pass rate by task</span>
              <span className="spacer" />
              {s.archs.length > 1 && (
                <div className="segmented" role="tablist" aria-label="architecture">
                  {s.archs.map((a) => (
                    <button key={a} role="tab" aria-selected={a === arch} className={a === arch ? "on" : ""} onClick={() => setTaskArch(a)}>
                      {a}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="panel-body">
              <Heatmap
                rows={s.tasks}
                cols={s.configs}
                rowHeader={s.archs.length > 1 ? undefined : arch}
                cell={(r, c) => taskGrid.get(`${arch}|${r}|${c}`)}
              />
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Trials</span>
              {selected && (
                <span className="secondary" style={{ fontSize: 12.5 }}>
                  <span className="mono">{selected[0]}</span> × <span className="mono">{selected[1]}</span>{" "}
                  <button className="link" onClick={() => setSelected(null)}>
                    clear
                  </button>
                </span>
              )}
              <span className="spacer" />
              <select
                aria-label="task filter"
                value={filters.task}
                onChange={(e) => setFilters({ ...filters, task: e.target.value })}
                style={{ height: 26, fontSize: 12.5 }}
              >
                <option value="">all tasks</option>
                {s.tasks.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              <select
                aria-label="outcome filter"
                value={filters.outcome}
                onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}
                style={{ height: 26, fontSize: 12.5 }}
              >
                <option value="">all outcomes</option>
                {[...OUTCOMES, "fail_harness"].map((o) => (
                  <option key={o} value={o}>
                    {OUTCOME_SHORT[o]}
                  </option>
                ))}
              </select>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>outcome</th>
                    <th>task</th>
                    <th>architecture</th>
                    <th>fault config</th>
                    <th className="r">rep</th>
                    <th>fault</th>
                    <th className="r">calls</th>
                    <th>cause</th>
                    <th>reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(trials.data ?? []).slice(0, shown).map((t) => (
                    <tr key={t.id} className="clickable" onClick={() => navigate(`/trials/${t.id}`)}>
                      <td>
                        <OutcomeStatus outcome={t.outcome} short />
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {t.task_id}
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {t.architecture_id}
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {t.fault_config}
                      </td>
                      <td className="r num">{t.repeat_idx}</td>
                      <td>{t.fault_fired ? <span className="status warning" title="a fault was injected"><Fault size={12} /> hit</span> : <span className="muted">–</span>}</td>
                      <td className="r num">{t.tool_calls}</td>
                      <td style={{ fontSize: 12.5 }} title={t.attribution?.evidence}>
                        {t.attribution ? (
                          <>
                            {CAUSE_LABEL[t.attribution.cause] ?? t.attribution.cause}
                            {t.attribution.subsystem && <span className="muted"> · {t.attribution.subsystem}</span>}
                          </>
                        ) : (
                          <span className="muted">–</span>
                        )}
                      </td>
                      <td className="secondary" style={{ fontSize: 12.5, maxWidth: 420 }}>
                        {t.reason}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {trials.data && trials.data.length === 0 && <div className="empty">No trials match.</div>}
              {trials.data && trials.data.length > shown && (
                <div style={{ padding: 10, textAlign: "center" }}>
                  <button className="small" onClick={() => setShown(shown + 100)}>
                    Show more ({trials.data.length - shown} remaining)
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
