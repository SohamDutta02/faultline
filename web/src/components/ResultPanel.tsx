import { Link } from "react-router-dom";
import { compact, ms } from "../format";
import type { TrialView, WorldDiff } from "../types";
import { CheckIcon, OutcomeHeadline } from "./Badges";

function Diff({ diff }: { diff: WorldDiff }) {
  const empty = !diff.inventory.length && !diff.orders_added.length && !diff.orders_changed.length;
  if (empty) return <p className="secondary">No changes to the world.</p>;
  return (
    <div className="stack" style={{ gap: 12 }}>
      {diff.inventory.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>stock</th>
              <th className="r">before</th>
              <th className="r">after</th>
              <th className="r">change</th>
            </tr>
          </thead>
          <tbody>
            {diff.inventory.map((row) => (
              <tr key={row.sku}>
                <td>
                  <span className="mono">{row.sku}</span> <span className="secondary">{row.name}</span>
                </td>
                <td className="r num">{row.before}</td>
                <td className="r num">{row.after}</td>
                <td className="r num">
                  {row.after - row.before > 0 ? "+" : ""}
                  {row.after - row.before}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {(diff.orders_added.length > 0 || diff.orders_changed.length > 0) && (
        <table>
          <thead>
            <tr>
              <th>order</th>
              <th>line</th>
              <th className="r">status</th>
            </tr>
          </thead>
          <tbody>
            {diff.orders_added.map((o) => (
              <tr key={o.order_id}>
                <td className="mono nowrap">{o.order_id}</td>
                <td>
                  <span className="mono">{o.customer_id}</span> · {o.qty} × <span className="mono">{o.sku}</span>{" "}
                  <span className="secondary num">= {o.total.toFixed(2)}</span>
                </td>
                <td className="r nowrap">new, {o.status}</td>
              </tr>
            ))}
            {diff.orders_changed.map((o) => (
              <tr key={o.order_id}>
                <td className="mono nowrap">{o.order_id}</td>
                <td>
                  <span className="mono">{o.customer_id}</span> · {o.qty} × <span className="mono">{o.sku}</span>
                </td>
                <td className="r nowrap">
                  {o.before} → {o.after}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function ResultPanel({ view, running }: { view: TrialView | null; running: boolean }) {
  if (!view) {
    return (
      <div className="panel">
        <div className="panel-head">
          <span className="label">Verdict</span>
        </div>
        <div className="empty">
          {running ? (
            <>
              <span className="spinner" style={{ display: "inline-block", verticalAlign: "middle", marginRight: 8 }} />
              grading starts when the agent finishes
            </>
          ) : (
            "The verifier grades the world the agent leaves behind, not what it says."
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-head">
        <span className="label">Verdict</span>
        <span className="spacer" />
        {view.trial_id && (
          <Link to={`/trials/${view.trial_id}`} style={{ fontSize: 12.5 }}>
            permalink
          </Link>
        )}
      </div>
      <div className="panel-body">
        <div className="section">
          <OutcomeHeadline outcome={view.outcome} />
          {view.reason && view.outcome !== "fail_wrong_answer" && (
            <p className="secondary" style={{ marginTop: 6, fontSize: 13 }}>
              {view.reason}
            </p>
          )}
        </div>

        {view.checks.length > 0 && (
          <div className="section">
            <span className="label">Checks</span>
            <ul className="checks">
              {view.checks.map((c) => (
                <li key={c.name}>
                  <CheckIcon passed={c.passed} />
                  <span>{c.name}</span>
                  {c.detail && <span className="detail">{c.detail}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="section">
          <span className="label">Final answer</span>
          {view.answer ? (
            <div className="body">
              <pre>{view.answer}</pre>
            </div>
          ) : (
            <p className="secondary">none</p>
          )}
        </div>

        <div className="section">
          <span className="label">Cost</span>
          <dl className="stats">
            <div>
              <dt>tool calls</dt>
              <dd>{view.tool_calls}</dd>
            </div>
            <div>
              <dt>tool errors</dt>
              <dd>{view.tool_errors}</dd>
            </div>
            <div>
              <dt>client retries</dt>
              <dd>{view.retries}</dd>
            </div>
            <div>
              <dt>model calls</dt>
              <dd>{view.llm_calls}</dd>
            </div>
            <div>
              <dt>tokens</dt>
              <dd>{compact(view.tokens_used)}</dd>
            </div>
            <div>
              <dt>duration</dt>
              <dd>{ms(view.duration_ms)}</dd>
            </div>
          </dl>
        </div>

        {view.world_diff && (
          <div className="section">
            <span className="label">World diff</span>
            <Diff diff={view.world_diff} />
          </div>
        )}
      </div>
    </div>
  );
}
