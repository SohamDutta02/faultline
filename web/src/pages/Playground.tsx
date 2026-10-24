import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api, followJob } from "../api";
import { FaultChip, OutcomeStatus } from "../components/Badges";
import { HarnessPicker } from "../components/HarnessPicker";
import { Play } from "../components/Icons";
import { ResultPanel } from "../components/ResultPanel";
import { Trace } from "../components/Trace";
import { faultSummary, ms, when } from "../format";
import { useFetch, useMeta } from "../hooks";
import type { Architecture, JobSummary, TraceEvent, TrialRow, TrialView } from "../types";

interface RunInfo {
  task_id: string;
  architecture_id: string;
  fault_config: string;
  seed: number;
}

export function Playground() {
  const meta = useMeta();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const jobId = params.get("job");

  const presetAvailable = (id: string) => {
    const a = meta.architectures.find((x) => x.id === id);
    return !!a && (!a.needs_llm || meta.llm_available);
  };
  const firstArch = meta.architectures.find((a) => presetAvailable(a.id))?.id ?? "oracle";

  const [taskId, setTaskId] = useState(params.get("task") ?? "t6_stock_fallback");
  const [archId, setArchId] = useState(params.get("arch") ?? firstArch);
  const archInfo = useFetch<Architecture>(`/api/architectures/${encodeURIComponent(archId)}`);
  const archAvailable = (id: string) =>
    id === archId && !!archInfo.data && (!archInfo.data.needs_llm || meta.llm_available);
  const [configName, setConfigName] = useState(params.get("fault") ?? "stale_data");
  const [seed, setSeed] = useState(params.get("seed") ?? "");

  const [run, setRun] = useState<RunInfo | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [result, setResult] = useState<TrialView | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  const recent = useFetch<TrialRow[]>("/api/playground/runs?limit=10");

  useEffect(() => {
    if (!jobId) return;
    setEvents([]);
    setResult(null);
    setError(null);
    setRunning(true);
    const stop = followJob(
      jobId,
      (ev) => {
        if (ev.type === "start") {
          const info = { task_id: ev.task_id, architecture_id: ev.architecture_id, fault_config: ev.fault_config, seed: ev.seed };
          setRun(info);
          setStartedAt(ev.t * 1000);
          setTaskId(info.task_id);
          setArchId(info.architecture_id);
          setConfigName(info.fault_config);
        } else if (ev.type === "event") {
          setEvents((prev) => [...prev, ev.event as TraceEvent]);
        } else if (ev.type === "result") {
          setResult(ev.result as TrialView);
        } else if (ev.type === "error") {
          setError(ev.message);
        } else if (ev.type === "end") {
          setRunning(false);
          recent.reload();
        }
      },
      () => {
        setRunning(false);
        setError("Lost the connection to this run. It may have been started before the server restarted.");
      },
    );
    return stop;
  }, [jobId]); // recent.reload is stable

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [running]);

  async function start() {
    setError(null);
    try {
      const job = await api.post<JobSummary>("/api/trials", {
        task_id: taskId,
        architecture_id: archId,
        fault_config: configName,
        seed: seed.trim() === "" ? null : Number(seed),
      });
      navigate(`/?job=${job.id}`, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const job = e.body.job as JobSummary;
        setError(`A ${job.kind} is already running (${job.title}). Wait for it or stop it first.`);
      } else {
        setError((e as Error).message);
      }
    }
  }

  const task = meta.tasks.find((t) => t.id === taskId);
  const config = meta.fault_configs.find((c) => c.name === configName);
  const elapsed = startedAt ? (running ? now : startedAt + (result?.duration_ms ?? 0)) - startedAt : 0;

  return (
    <div className="page">
      <div className="workbench">
        <div className="stack">
          <div className="panel">
            <div className="panel-body stack" style={{ gap: 10 }}>
              <div className="field-row">
                <label className="field" style={{ flex: 1 }}>
                  <span className="label">Seed</span>
                  <input
                    type="number"
                    placeholder="random"
                    value={seed}
                    onChange={(e) => setSeed(e.target.value)}
                    disabled={running}
                  />
                </label>
                <button
                  className="primary"
                  onClick={start}
                  disabled={running || !archAvailable(archId)}
                  style={{ flex: 1 }}
                >
                  <Play size={12} />
                  {running ? "Running" : "Run trial"}
                </button>
              </div>
              <p className="secondary" style={{ fontSize: 12 }}>
                The seed fixes which calls the proxy faults. Leave it empty for a new schedule each run.
              </p>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Task</span>
            </div>
            <div className="panel-body">
              <div className="options" role="radiogroup" aria-label="task">
                {meta.tasks.map((t) => (
                  <label key={t.id} className={`option${t.id === taskId ? " selected" : ""}`}>
                    <input type="radio" name="task" checked={t.id === taskId} onChange={() => setTaskId(t.id)} disabled={running} />
                    <span className="option-title">
                      <span className="mono">{t.id}</span>
                      <span className="tag">{t.category.replaceAll("_", " ")}</span>
                    </span>
                  </label>
                ))}
              </div>
              {task && (
                <div className="prompt">
                  {task.prompt}
                  {task.setup.length > 0 && (
                    <div className="secondary" style={{ marginTop: 6, fontSize: 12 }}>
                      Starts with {task.setup.length} existing orders.
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Harness</span>
              <span className="spacer" />
              <Link to="/catalog#harness" style={{ fontSize: 12.5 }}>
                subsystems
              </Link>
            </div>
            <div className="panel-body">
              <HarnessPicker value={archId} onChange={setArchId} disabled={running} />
              {archInfo.error && <div className="notice error" style={{ marginTop: 10 }}>{archInfo.error}</div>}
              {archInfo.data?.needs_llm && !meta.llm_available && (
                <p className="secondary" style={{ fontSize: 12, marginTop: 8 }}>
                  This harness drives a model: set GEMINI_API_KEY to run it.
                </p>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Fault config</span>
            </div>
            <div className="panel-body">
              <div className="options" role="radiogroup" aria-label="fault config">
                {meta.fault_configs.map((c) => (
                  <label key={c.name} className={`option${c.name === configName ? " selected" : ""}`}>
                    <input
                      type="radio"
                      name="fault"
                      checked={c.name === configName}
                      onChange={() => setConfigName(c.name)}
                      disabled={running}
                    />
                    <span className="option-title mono">{c.name}</span>
                    <span className="option-desc">{faultSummary(c.faults)}</span>
                  </label>
                ))}
              </div>
              {config && config.faults.length > 0 && (
                <div className="stack" style={{ gap: 8, marginTop: 10 }}>
                  {config.faults.map((f, i) => (
                    <div key={i} style={{ fontSize: 12.5 }}>
                      <FaultChip fault={f.type} /> <span className="secondary">{f.layer} · {f.phase} phase</span>
                      <div className="secondary" style={{ marginTop: 3 }}>
                        {meta.fault_types[f.type]?.description}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="label">Recent runs</span>
            </div>
            {recent.data && recent.data.length > 0 ? (
              <div className="table-wrap">
                <table>
                  <tbody>
                    {recent.data.map((r) => (
                      <tr key={r.id} className="clickable" onClick={() => navigate(`/trials/${r.id}`)}>
                        <td>
                          <OutcomeStatus outcome={r.outcome} short />
                        </td>
                        <td>
                          <div className="mono" style={{ fontSize: 12 }}>
                            {r.task_id}
                          </div>
                          <div className="secondary" style={{ fontSize: 12 }}>
                            {r.architecture_id} · {r.fault_config}
                          </div>
                        </td>
                        <td className="r secondary" style={{ fontSize: 12 }}>
                          {when(r.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty" style={{ padding: 16 }}>
                No runs yet.
              </div>
            )}
          </div>
        </div>

        <div className="stack">
          {error && <div className="notice error">{error}</div>}
          <div className="panel">
            <div className="panel-head">
              <span className="label">Trace</span>
              {run && (
                <span className="secondary" style={{ fontSize: 12.5 }}>
                  <span className="mono">{run.task_id}</span> · {run.architecture_id} · {run.fault_config} · seed{" "}
                  <span className="mono">{run.seed}</span>
                </span>
              )}
              <span className="spacer" />
              {run && (
                <span className="secondary num" style={{ fontSize: 12.5, display: "inline-flex", gap: 6, alignItems: "center" }}>
                  {running && <span className="spinner" />}
                  {running ? ms(elapsed) : result ? `finished in ${ms(result.duration_ms)}` : ""}
                </span>
              )}
            </div>
            {run || running ? (
              <Trace events={events} live={running} />
            ) : (
              <div className="empty" style={{ padding: "48px 24px" }}>
                <p>Pick a task, a harness and a fault config, then run a trial.</p>
                <p className="secondary" style={{ marginTop: 6 }}>
                  Every tool call goes through the fault proxy. Calls it tampered with are marked{" "}
                  <FaultChip fault="fault" />. For the full matrix, start a <Link to="/sweeps">sweep</Link>.
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="result-col">
          <ResultPanel view={result} running={running} />
        </div>
      </div>
    </div>
  );
}
