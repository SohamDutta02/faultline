import { FaultChip } from "../components/Badges";
import { useMeta } from "../hooks";

function Diagram() {
  // runner -> agent -> proxy -> toolserver, with the verifier reading snapshots
  const box = { rx: 4, fill: "var(--surface)", stroke: "var(--line)" };
  const text = { fontFamily: "var(--mono)", fontSize: 12, fill: "var(--ink)" };
  const small = { fontFamily: "var(--sans)", fontSize: 11, fill: "var(--ink-2)" };
  return (
    <svg className="diagram" viewBox="0 0 760 200" role="img" aria-label="trial pipeline">
      <defs>
        <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L8 4L0 8z" fill="var(--ink-2)" />
        </marker>
      </defs>
      <g stroke="var(--ink-2)" strokeWidth="1" fill="none" markerEnd="url(#arr)">
        <path d="M120 60h52" />
        <path d="M312 52h60" />
        <path d="M372 68h-60" />
        <path d="M512 52h60" />
        <path d="M572 68h-60" />
        <path d="M642 80v60" />
        <path d="M242 80v60" />
      </g>
      <rect x="10" y="40" width="110" height="40" {...box} />
      <text x="65" y="64" textAnchor="middle" {...text}>runner</text>
      <rect x="172" y="40" width="140" height="40" {...box} />
      <text x="242" y="58" textAnchor="middle" {...text}>agent</text>
      <text x="242" y="72" textAnchor="middle" {...small}>one architecture</text>
      <rect x="372" y="40" width="140" height="40" rx="4" fill="var(--fault-wash)" stroke="var(--warning)" />
      <text x="442" y="58" textAnchor="middle" {...text}>fault proxy</text>
      <text x="442" y="72" textAnchor="middle" {...small}>injects faults</text>
      <rect x="572" y="40" width="140" height="40" {...box} />
      <text x="642" y="58" textAnchor="middle" {...text}>toolserver</text>
      <text x="642" y="72" textAnchor="middle" {...small}>seeded warehouse</text>
      <rect x="172" y="140" width="540" height="40" {...box} />
      <text x="442" y="164" textAnchor="middle" {...text}>verifier: checks over before/after snapshots + answer</text>
      <text x="146" y="32" textAnchor="middle" {...small}>task prompt</text>
      <text x="342" y="32" textAnchor="middle" {...small}>tool calls</text>
      <text x="542" y="32" textAnchor="middle" {...small}>forwarded</text>
      <text x="72" y="118" {...small}>reset, snapshot</text>
      <text x="248" y="118" {...small}>final answer</text>
      <text x="648" y="118" {...small}>world state</text>
      <path d="M65 80v80h105" stroke="var(--ink-2)" fill="none" markerEnd="url(#arr)" />
    </svg>
  );
}

export function Catalog() {
  const meta = useMeta();
  return (
    <div className="page stack">
      <div>
        <h1 style={{ marginBottom: 6 }}>How a trial works</h1>
        <p className="secondary" style={{ maxWidth: 760 }}>
          The runner resets a seeded warehouse and snapshots it. The agent works the task through a proxy that breaks tool
          traffic according to the active fault config. When the agent stops, the runner snapshots the world again and
          task-specific checks grade the result. A write counts if the world shows it, whatever the agent says.
        </p>
      </div>
      <div className="panel">
        <div className="panel-body">
          <Diagram />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">Fault configs</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>config</th>
                <th>fault</th>
                <th>layer</th>
                <th>phase</th>
                <th>targets</th>
                <th className="r">rate</th>
                <th>effect</th>
              </tr>
            </thead>
            <tbody>
              {meta.fault_configs.map((c) =>
                c.faults.length === 0 ? (
                  <tr key={c.name}>
                    <td className="mono">{c.name}</td>
                    <td colSpan={6} className="secondary">
                      no faults; the baseline every other config is compared with
                    </td>
                  </tr>
                ) : (
                  c.faults.map((f, i) => (
                    <tr key={`${c.name}-${i}`}>
                      <td className="mono">{i === 0 ? c.name : ""}</td>
                      <td>
                        <FaultChip fault={f.type} />
                      </td>
                      <td>{f.layer}</td>
                      <td>{f.phase === "request" ? "request, not forwarded" : "response, after forwarding"}</td>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {f.targets.join(", ")}
                      </td>
                      <td className="r num">{f.max_fires ? `first ${f.max_fires}` : `${Math.round(f.rate * 100)}%`}</td>
                      <td className="secondary">{meta.fault_types[f.type]?.description}</td>
                    </tr>
                  ))
                ),
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">Tasks</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>task</th>
                <th>category</th>
                <th>prompt</th>
                <th>checks</th>
              </tr>
            </thead>
            <tbody>
              {meta.tasks.map((t) => (
                <tr key={t.id}>
                  <td className="mono">{t.id}</td>
                  <td>{t.category.replaceAll("_", " ")}</td>
                  <td style={{ maxWidth: 440 }}>{t.prompt}</td>
                  <td className="secondary">{t.checks.join("; ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="label">Architectures</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>architecture</th>
                <th>driven by</th>
                <th>client retries</th>
                <th>design</th>
              </tr>
            </thead>
            <tbody>
              {meta.architectures.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.id}</td>
                  <td>{a.needs_llm ? (meta.llm_available ? meta.model : "model (not configured)") : "script"}</td>
                  <td>{a.retries ? "reads on 429/5xx/bad JSON, writes on 429" : "none"}</td>
                  <td className="secondary">{a.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
