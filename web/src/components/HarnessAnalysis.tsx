import { CAUSE_LABEL, signed, tone } from "../harness";
import { compact } from "../format";
import type { SweepSummary, VersionInfo } from "../types";
import { Alert } from "./Icons";

function describe(v: VersionInfo): string | null {
  const notes: string[] = [];
  if (v.stale) notes.push(`${v.stale} trials come from an older harness (${v.changed.join(", ") || "something"} changed since)`);
  if (v.versions > 1) notes.push(`results mix ${v.versions} harness versions`);
  if (v.unknown) notes.push(`${v.unknown} trials predate harness fingerprints`);
  return notes.length ? notes.join("; ") : null;
}

export function VersionNotes({ s }: { s: SweepSummary }) {
  const notes = s.versions.map((v) => [v.arch, describe(v)] as const).filter(([, d]) => d);
  if (!notes.length) return null;
  return (
    <div className="notice" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 4 }}>
        <Alert size={14} /> <b>Some results don't describe today's harness</b>
      </div>
      {notes.map(([arch, d]) => (
        <div key={arch} style={{ fontSize: 12.5 }}>
          <span className="mono">{arch}</span>: {d}
        </div>
      ))}
      <div className="secondary" style={{ fontSize: 12, marginTop: 4 }}>
        Resume with rerun-stale to redo those cells under the current harness, or compare against a fresh sweep.
      </div>
    </div>
  );
}

function Delta({ value, digits = 0 }: { value: number; digits?: number }) {
  return <span className={`num delta ${tone(value)}`}>{signed(value, digits)}</span>;
}

export function SpreadPanel({ s }: { s: SweepSummary }) {
  if (!s.spread.length) return null;
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="label">Harness spread</span>
        <span className="secondary" style={{ fontSize: 12.5 }}>
          same model, best vs worst harness: how much of the outcome the harness decides
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>fault config</th>
              <th>best harness</th>
              <th className="r">pass</th>
              <th>worst harness</th>
              <th className="r">pass</th>
              <th className="r">spread</th>
              <th style={{ width: "22%" }} />
            </tr>
          </thead>
          <tbody>
            {s.spread.map((r) => (
              <tr key={r.config}>
                <td className="mono">{r.config === "all" ? <b>all faults</b> : r.config}</td>
                <td className="mono" style={{ fontSize: 12 }}>{r.best}</td>
                <td className="r num">{Math.round(r.best_rate)}%</td>
                <td className="mono" style={{ fontSize: 12 }}>{r.worst}</td>
                <td className="r num">{Math.round(r.worst_rate)}%</td>
                <td className="r num">{Math.round(r.spread)} pts</td>
                <td style={{ verticalAlign: "middle" }}>
                  <div className="meter">
                    <span style={{ width: `${r.spread}%` }} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ComparisonsPanel({ s }: { s: SweepSummary }) {
  if (!s.comparisons.length) return null;
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="label">Controlled comparisons</span>
        <span className="secondary" style={{ fontSize: 12.5 }}>
          harnesses that differ in exactly one subsystem, paired cell by cell (same fault schedule)
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>subsystem</th>
              <th>change</th>
              <th>from → to</th>
              <th className="r">pairs</th>
              <th className="r">pass (pts)</th>
              {s.configs.map((c) => (
                <th key={c} className="r mono" style={{ fontSize: 11.5 }}>{c}</th>
              ))}
              <th className="r">tokens</th>
              <th className="r">tool calls</th>
            </tr>
          </thead>
          <tbody>
            {s.comparisons.map((c, i) => (
              <tr key={i}>
                <td>{c.slot}</td>
                <td className="mono nowrap" style={{ fontSize: 12 }}>
                  {c.from_option} → {c.to_option}
                </td>
                <td className="mono" style={{ fontSize: 12, maxWidth: 320 }}>
                  {c.from_arch} → {c.to_arch}
                </td>
                <td className="r num">{c.n}</td>
                <td className="r"><Delta value={c.delta} /></td>
                {s.configs.map((cfg) => (
                  <td key={cfg} className="r">
                    {c.by_config[cfg] ? <Delta value={c.by_config[cfg].delta} /> : <span className="muted">–</span>}
                  </td>
                ))}
                <td className="r num">{signed(c.d_tokens)}</td>
                <td className="r num">{signed(c.d_tool_calls, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {s.interactions.length > 0 && (
        <div className="panel-body" style={{ borderTop: "1px solid var(--grid)" }}>
          <span className="label">Interactions</span>
          <ul className="findings">
            {s.interactions.map((x, i) => (
              <li key={i} style={{ display: "block", fontSize: 12.5 }}>
                <b>{x.slot}</b> <span className="mono">{x.from_option} → {x.to_option}</span>{" "}
                {x.config === "all" ? "overall" : <>under <span className="mono">{x.config}</span></>}:{" "}
                {x.contexts.map((c, j) => (
                  <span key={j}>
                    {j > 0 && "; "}
                    <Delta value={c.delta} /> from <span className="mono">{c.from_arch}</span>
                  </span>
                ))}
                . Its effect depends on the rest of the harness.
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const SUBSYSTEMS = ["instructions", "tools", "environment", "state", "feedback", "none"];

export function AttributionPanel({ s }: { s: SweepSummary }) {
  const rows = s.attribution.filter((a) => a.failures > 0);
  if (!rows.length) return null;
  const causes = Object.keys(CAUSE_LABEL).filter((c) => rows.some((r) => r.causes[c]));
  const subs = SUBSYSTEMS.filter((x) => rows.some((r) => r.subsystems[x]));
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="label">Failure attribution</span>
        <span className="secondary" style={{ fontSize: 12.5 }}>
          why each failure happened, and which subsystem should have prevented it
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>architecture</th>
              <th className="r">failures</th>
              {causes.map((c) => (
                <th key={c} className="r">{CAUSE_LABEL[c]}</th>
              ))}
              <th style={{ borderLeft: "1px solid var(--grid)" }}>share by subsystem</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.arch}>
                <td className="mono" style={{ fontSize: 12 }}>{r.arch}</td>
                <td className="r num">{r.failures}</td>
                {causes.map((c) => (
                  <td key={c} className="r num">{r.causes[c] || <span className="muted">0</span>}</td>
                ))}
                <td style={{ borderLeft: "1px solid var(--grid)", fontSize: 12.5 }}>
                  {subs
                    .filter((x) => r.subsystems[x])
                    .map((x) => `${x === "none" ? "bug" : x} ${Math.round((100 * r.subsystems[x]) / r.failures)}%`)
                    .join(" · ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function FeedbackPanel({ s }: { s: SweepSummary }) {
  if (!s.feedback.length) return null;
  const pct = (k: number, n: number) => `${Math.round((100 * k) / n)}%`;
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="label">Feedback: first answer vs final</span>
        <span className="secondary" style={{ fontSize: 12.5 }}>
          the first answer is graded before any review, so this is what feedback changed
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>architecture</th>
              <th className="r">trials</th>
              <th className="r">first answer</th>
              <th className="r">final</th>
              <th className="r">fixed</th>
              <th className="r">broke</th>
              <th className="r">flagged</th>
              <th className="r">false alarms</th>
              <th className="r">missed</th>
              <th className="r">extra tool calls</th>
              <th className="r">extra tokens</th>
            </tr>
          </thead>
          <tbody>
            {s.feedback.map((f) => (
              <tr key={f.arch}>
                <td className="mono" style={{ fontSize: 12 }}>{f.arch}</td>
                <td className="r num">{f.n}</td>
                <td className="r num">{pct(f.first_pass, f.n)}</td>
                <td className="r num">{pct(f.final, f.n)}</td>
                <td className="r num">{f.fixed}</td>
                <td className="r num">{f.broke}</td>
                <td className="r num">{f.flagged}</td>
                <td className="r num">{f.false_alarms}</td>
                <td className="r num">{f.missed}</td>
                <td className="r num">{f.extra_tool_calls.toFixed(1)}</td>
                <td className="r num">{compact(f.extra_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
