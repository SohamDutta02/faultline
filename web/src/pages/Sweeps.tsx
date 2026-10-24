import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, api } from "../api";
import { JobStatus } from "../components/Badges";
import { Play } from "../components/Icons";
import { defaultLabel, pct, when } from "../format";
import { useFetch, useMeta } from "../hooks";
import type { Architecture, JobSummary, SweepRow } from "../types";

function Checklist({
  name,
  items,
  selected,
  onChange,
  disabled,
  describe,
}: {
  name: string;
  items: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  disabled?: (item: string) => boolean;
  describe?: (item: string) => string | undefined;
}) {
  const toggle = (item: string) =>
    onChange(selected.includes(item) ? selected.filter((x) => x !== item) : items.filter((x) => x === item || selected.includes(x)));
  return (
    <fieldset style={{ border: "none", margin: 0, padding: 0, minWidth: 0 }}>
      <legend className="label" style={{ marginBottom: 6 }}>
        {name}{" "}
        <span className="muted" style={{ fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
          {selected.length} of {items.length}
        </span>
      </legend>
      <div className="options">
        {items.map((item) => {
          const off = disabled?.(item) ?? false;
          return (
            <label key={item} className={`option${off ? " disabled" : ""}`}>
              <input type="checkbox" checked={selected.includes(item)} disabled={off} onChange={() => toggle(item)} />
              <span className="option-title mono">{item}</span>
              {describe?.(item) && <span className="option-desc">{describe(item)}</span>}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function NewSweep({ onCancel }: { onCancel: () => void }) {
  const meta = useMeta();
  const navigate = useNavigate();
  const usable = meta.architectures.filter((a) => !a.needs_llm || meta.llm_available).map((a) => a.id);
  const [label, setLabel] = useState(defaultLabel());
  const [archs, setArchs] = useState<string[]>(meta.llm_available ? meta.sweep_defaults.archs : usable);
  const [configs, setConfigs] = useState<string[]>(meta.fault_configs.map((c) => c.name));
  const [tasks, setTasks] = useState<string[]>(meta.tasks.map((t) => t.id));
  const [repeats, setRepeats] = useState(3);
  const [pace, setPace] = useState(15);
  const [ablate, setAblate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);

  const ablation = useFetch<Architecture[]>(ablate ? `/api/ablation/${encodeURIComponent(ablate)}` : null);
  const ablated = ablate && ablation.data ? ablation.data : [];
  const allArchs = [...new Set([...archs, ...ablated.map((a) => a.id)])];
  const usesModel =
    archs.some((a) => meta.architectures.find((x) => x.id === a)?.needs_llm) || ablated.some((a) => a.needs_llm);
  const total = allArchs.length * configs.length * tasks.length * repeats;

  async function start(onStale: "refuse" | "rerun" | "mix" = "refuse") {
    setBusy(true);
    setError(null);
    setStale(null);
    try {
      await api.post<JobSummary>("/api/sweeps", {
        label, archs, configs, tasks, repeats, pace: usesModel ? pace : 0, ablate: ablate || null, on_stale: onStale,
      });
      navigate(`/sweeps/${encodeURIComponent(label)}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.body?.job) {
        setError(`Busy: ${e.body.job.kind} ${e.body.job.title} is running.`);
      } else if (e instanceof ApiError && e.status === 409 && e.body?.detail?.stale) {
        setStale(e.body.detail.stale as string[]);
      } else {
        setError((e as Error).message);
      }
      setBusy(false);
    }
  }

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head">
        <span className="label">New sweep</span>
      </div>
      <div className="panel-body stack">
        <div className="field-row">
          <label className="field" style={{ minWidth: 260 }}>
            <span className="label">Label</span>
            <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} className="mono" />
          </label>
          <label className="field" style={{ width: 90 }}>
            <span className="label">Repeats</span>
            <input type="number" min={1} max={20} value={repeats} onChange={(e) => setRepeats(Number(e.target.value))} />
          </label>
          <label className="field" style={{ width: 110 }}>
            <span className="label">Pace (s)</span>
            <input
              type="number"
              min={0}
              value={pace}
              disabled={!usesModel}
              onChange={(e) => setPace(Number(e.target.value))}
              title="pause between model trials, for API rate limits"
            />
          </label>
        </div>
        <div className="field-row" style={{ alignItems: "flex-start" }}>
          <label className="field" style={{ minWidth: 260 }}>
            <span className="label">Ablate a harness</span>
            <select value={ablate} onChange={(e) => setAblate(e.target.value)}>
              <option value="">none</option>
              {meta.architectures
                .filter((a) => a.id !== "single" && a.id !== "oracle")
                .map((a) => (
                  <option key={a.id} value={a.id} disabled={a.needs_llm && !meta.llm_available}>
                    {a.id}
                  </option>
                ))}
            </select>
          </label>
          <div className="secondary" style={{ fontSize: 12.5, flex: 1, paddingTop: 18 }}>
            {ablate && ablation.data ? (
              <>
                Adds {ablation.data.length} harnesses: the baseline, the baseline plus each subsystem{" "}
                <span className="mono">{ablate}</span> changes, <span className="mono">{ablate}</span> minus each, and{" "}
                <span className="mono">{ablate}</span>:{" "}
                <span className="mono">{ablation.data.map((a) => a.id).join(", ")}</span>
              </>
            ) : ablation.error ? (
              <span style={{ color: "var(--critical-text)" }}>{ablation.error}</span>
            ) : (
              "Adds controlled-variable harnesses around one harness, so the report can measure each subsystem."
            )}
          </div>
        </div>
        <div className="grid-2" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
          <Checklist
            name="Architectures"
            items={meta.architectures.map((a) => a.id)}
            selected={archs}
            onChange={setArchs}
            disabled={(a) => !usable.includes(a)}
            describe={(a) => (usable.includes(a) ? undefined : "needs GEMINI_API_KEY")}
          />
          <Checklist name="Fault configs" items={meta.fault_configs.map((c) => c.name)} selected={configs} onChange={setConfigs} />
          <Checklist name="Tasks" items={meta.tasks.map((t) => t.id)} selected={tasks} onChange={setTasks} />
        </div>
        {error && <div className="notice error">{error}</div>}
        {stale && (
          <div className="notice">
            <b>Sweep {label} has results from older harness versions.</b>
            {stale.map((line) => (
              <div key={line} style={{ fontSize: 12.5 }}>{line}</div>
            ))}
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button className="small" onClick={() => start("rerun")}>Re-run those cells</button>
              <button className="small" onClick={() => start("mix")}>Keep them and mix versions</button>
            </div>
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button className="primary" onClick={() => start()} disabled={busy || total === 0}>
            <Play size={12} /> Start sweep
          </button>
          <button onClick={onCancel}>Cancel</button>
          <span className="secondary num" style={{ fontSize: 12.5 }}>
            {allArchs.length} × {configs.length} × {tasks.length} × {repeats} = {total} trials
            {usesModel && total > 1 ? `, at least ${Math.ceil(((total - 1) * pace) / 60)} min of pacing` : ""}
          </span>
        </div>
      </div>
    </div>
  );
}

export function Sweeps() {
  const [creating, setCreating] = useState(false);
  const { data: sweeps, error } = useFetch<SweepRow[]>("/api/sweeps", 3000);
  const navigate = useNavigate();

  return (
    <div className="page">
      <div className="page-head">
        <h1>Sweeps</h1>
        <span className="secondary">Every task × architecture × fault config, repeated, graded and stored.</span>
        <div className="actions">
          {sweeps && sweeps.length > 1 && <Link className="button" to="/compare">Compare sweeps</Link>}
          {!creating && (
            <button className="primary" onClick={() => setCreating(true)}>
              New sweep
            </button>
          )}
        </div>
      </div>

      {creating && <NewSweep onCancel={() => setCreating(false)} />}
      {error && <div className="notice error">{error}</div>}

      <div className="panel">
        {sweeps && sweeps.length === 0 ? (
          <div className="empty">
            No sweeps yet. A control sweep with the scripted architectures runs in seconds and needs no API key.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>label</th>
                  <th>status</th>
                  <th>model</th>
                  <th className="r">trials</th>
                  <th className="r">pass rate</th>
                  <th>matrix</th>
                  <th className="r">started</th>
                </tr>
              </thead>
              <tbody>
                {(sweeps ?? []).map((s) => {
                  const graded = s.trials - (s.harness ?? 0);
                  return (
                    <tr key={s.id} className="clickable" onClick={() => navigate(`/sweeps/${encodeURIComponent(s.label)}`)}>
                      <td>
                        <Link to={`/sweeps/${encodeURIComponent(s.label)}`} className="mono" onClick={(e) => e.stopPropagation()}>
                          {s.label}
                        </Link>
                      </td>
                      <td>
                        <JobStatus status={s.live ? "running" : s.status} />
                      </td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {s.model_id}
                      </td>
                      <td className="r num">
                        {s.trials}
                        {s.planned ? <span className="muted"> / {s.planned}</span> : null}
                      </td>
                      <td className="r num">{pct(s.passes ?? 0, graded)}</td>
                      <td className="secondary" style={{ fontSize: 12.5 }}>
                        {s.params
                          ? `${s.params.archs.length} archs · ${s.params.configs.length} configs · ${s.params.tasks.length} tasks · ×${s.params.repeats}`
                          : "–"}
                      </td>
                      <td className="r secondary">{when(s.started_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
