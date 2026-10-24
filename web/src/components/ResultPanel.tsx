import { Link } from "react-router-dom";
import { compact, ms } from "../format";
import { CAUSE_LABEL } from "../harness";
import type { Attribution, FeedbackRecord, TrialView, WorldDiff } from "../types";
import { CheckIcon, OutcomeHeadline, OutcomeStatus } from "./Badges";

function AttributionView({ a }: { a: Attribution }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
        <b>{CAUSE_LABEL[a.cause] ?? a.cause}</b>
        {a.subsystem ? (
          <span className="tag" title="the harness subsystem whose job it was to prevent this">
            {a.subsystem}
            {a.also.length > 0 ? ` (or ${a.also.join(", ")})` : ""}
          </span>
        ) : (
          <span className="tag">harness bug</span>
        )}
      </div>
      <div className="secondary" style={{ fontSize: 12.5 }}>{a.evidence}</div>
      {a.notes.map((n) => (
        <div key={n} className="secondary" style={{ fontSize: 12.5 }}>
          Note: {n}.
        </div>
      ))}
    </div>
  );
}

function FeedbackView({ fb, final }: { fb: FeedbackRecord; final: TrialView["outcome"] }) {
  const first = fb.first_pass.outcome;
  const story =
    first === "pass" && final !== "pass"
      ? "Feedback broke a passing answer."
      : first !== "pass" && final === "pass"
        ? "Feedback fixed a failing answer."
        : first === "pass"
          ? "The first answer passed and stayed passing."
          : "The first answer failed and feedback didn't fix it.";
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="kv" style={{ gap: 12 }}>
        <span>
          first answer <OutcomeStatus outcome={first} short />
        </span>
        <span>
          reviews{" "}
          <b className="mono">{fb.reviews.map((r) => r.verdict).join(" → ") || "none"}</b>
        </span>
        <span>
          by <b className="mono">{fb.option}</b>
        </span>
      </div>
      <div className="secondary" style={{ fontSize: 12.5 }}>{story}</div>
    </div>
  );
}

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

        {view.attribution && (
          <div className="section">
            <span className="label">Why it failed</span>
            <AttributionView a={view.attribution} />
          </div>
        )}

        {view.feedback && (
          <div className="section">
            <span className="label">Feedback</span>
            <FeedbackView fb={view.feedback} final={view.outcome} />
          </div>
        )}

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
