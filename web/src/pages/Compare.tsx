import { Link, useSearchParams } from "react-router-dom";
import { signed, tone } from "../harness";
import { useFetch } from "../hooks";
import type { CompareCell, CompareResult, SweepRow } from "../types";

function Cells({ title, cells, empty }: { title: string; cells: CompareCell[]; empty: string }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="label">{title}</span>
      </div>
      {cells.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>architecture</th>
                <th>fault config</th>
                <th className="r">pairs</th>
                <th className="r">base</th>
                <th className="r">head</th>
                <th className="r">change</th>
              </tr>
            </thead>
            <tbody>
              {cells.map((c) => (
                <tr key={`${c.arch}|${c.config}`}>
                  <td className="mono" style={{ fontSize: 12 }}>{c.arch}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{c.config}</td>
                  <td className="r num">{c.pairs}</td>
                  <td className="r num">{Math.round(c.base_rate)}%</td>
                  <td className="r num">{Math.round(c.head_rate)}%</td>
                  <td className="r">
                    <span className={`num delta ${tone(c.delta)}`}>{signed(c.delta)} pts</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty" style={{ padding: 16 }}>{empty}</div>
      )}
    </div>
  );
}

export function Compare() {
  const [params, setParams] = useSearchParams();
  const base = params.get("base") ?? "";
  const head = params.get("head") ?? "";
  const { data: sweeps } = useFetch<SweepRow[]>("/api/sweeps");
  const ready = base && head;
  const { data: c, error } = useFetch<CompareResult>(
    ready ? `/api/compare?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}` : null,
  );
  const pick = (key: "base" | "head", value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  return (
    <div className="page stack">
      <div className="page-head">
        <h1>Compare sweeps</h1>
        <span className="secondary">
          The regression report for a harness change: run the same matrix before and after, then compare cell by cell.
        </span>
      </div>

      <div className="panel">
        <div className="panel-body field-row">
          {(["base", "head"] as const).map((key) => (
            <label key={key} className="field" style={{ minWidth: 260 }}>
              <span className="label">{key === "base" ? "Base (before)" : "Head (after)"}</span>
              <select value={key === "base" ? base : head} onChange={(e) => pick(key, e.target.value)}>
                <option value="">pick a sweep</option>
                {(sweeps ?? []).map((s) => (
                  <option key={s.id} value={s.label}>{s.label}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </div>

      {error && <div className="notice error">{error}</div>}
      {!ready && <div className="empty">Pick two sweeps.</div>}
      {c && (
        <>
          {c.base.model !== c.head.model && (
            <div className="notice">
              The model changed ({c.base.model} → {c.head.model}), so not every difference is the harness.
            </div>
          )}
          <div className="panel">
            <div className="panel-head">
              <span className="label">Pass rate on paired cells</span>
              <span className="secondary" style={{ fontSize: 12.5 }}>
                same task, fault config and repeat in both sweeps, so the same fault schedule
              </span>
            </div>
            {c.archs.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>architecture</th>
                      <th className="r">pairs</th>
                      <th className="r">base</th>
                      <th className="r">head</th>
                      <th className="r">change</th>
                      <th>harness</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.archs.map((a) => (
                      <tr key={a.arch}>
                        <td className="mono" style={{ fontSize: 12 }}>{a.arch}</td>
                        <td className="r num">{a.pairs}</td>
                        <td className="r num">{Math.round(a.base_rate)}%</td>
                        <td className="r num">{Math.round(a.head_rate)}%</td>
                        <td className="r">
                          <span className={`num delta ${tone(a.delta)}`}>{signed(a.delta)} pts</span>
                        </td>
                        <td style={{ fontSize: 12.5 }}>
                          {a.harness === "changed" ? (
                            <>changed: <b>{a.changed.join(", ")}</b></>
                          ) : a.harness === "same" ? (
                            <span className="secondary">same version</span>
                          ) : (
                            <span className="muted">unknown (recorded before fingerprints)</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">No architecture has paired cells in both sweeps.</div>
            )}
          </div>
          <div className="grid-2">
            <Cells title={`Regressions (${c.threshold} pts or more)`} cells={c.regressions} empty="None." />
            <Cells title={`Improvements (${c.threshold} pts or more)`} cells={c.improvements} empty="None." />
          </div>
          {(c.only_base.length > 0 || c.only_head.length > 0) && (
            <p className="secondary" style={{ fontSize: 12.5 }}>
              Only in <Link to={`/sweeps/${encodeURIComponent(base)}`}>{base}</Link>:{" "}
              <span className="mono">{c.only_base.join(", ") || "–"}</span>. Only in{" "}
              <Link to={`/sweeps/${encodeURIComponent(head)}`}>{head}</Link>:{" "}
              <span className="mono">{c.only_head.join(", ") || "–"}</span>.
            </p>
          )}
        </>
      )}
    </div>
  );
}
