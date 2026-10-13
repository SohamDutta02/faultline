import { useMeta } from "../hooks";
import { argList, ms } from "../format";
import type { TraceEvent } from "../types";
import { FaultChip, HttpStatus } from "./Badges";
import { JsonBody } from "./JsonView";
import { Retry } from "./Icons";

interface Attempt {
  status: number | null;
  body: string;
  latency: number;
  fault: string | null;
  retryDelay?: number;
}

type Block =
  | {
      type: "model";
      step: number;
      role: string;
      text: string;
      calls: { name: string; args: Record<string, unknown> }[];
      tokens: number;
      finish: string | null;
    }
  | { type: "call"; step: number; role: string; tool: string; args: Record<string, unknown>; attempts: Attempt[] }
  | { type: "review"; step: number; round: number; verdict: string; review: string }
  | { type: "crash"; step: number; traceback: string };

/** Fold the flat event log into what a reader follows: model turns and tool calls with their attempts. */
export function buildBlocks(events: TraceEvent[]): Block[] {
  const blocks: Block[] = [];
  let call: Extract<Block, { type: "call" }> | null = null;
  let role = "agent";
  for (const e of events) {
    const p = e.payload ?? {};
    switch (e.kind) {
      case "llm":
        role = p.role ?? "agent";
        call = null;
        blocks.push({
          type: "model",
          step: e.step_idx,
          role,
          text: p.text ?? "",
          calls: p.calls ?? [],
          tokens: p.tokens ?? 0,
          finish: p.finish ?? null,
        });
        break;
      case "tool_call":
        call = { type: "call", step: e.step_idx, role, tool: e.tool_name ?? "?", args: p, attempts: [] };
        blocks.push(call);
        break;
      case "tool_result": {
        const attempt: Attempt = {
          status: p.status ?? null,
          body: p.body ?? "",
          latency: e.latency_ms,
          fault: p.fault ?? (e.fault_injected ? "injected" : null),
        };
        if (!call) {
          call = { type: "call", step: e.step_idx, role, tool: e.tool_name ?? "?", args: {}, attempts: [] };
          blocks.push(call);
        }
        call.attempts.push(attempt);
        break;
      }
      case "retry":
        if (call?.attempts.length) call.attempts[call.attempts.length - 1].retryDelay = p.delay_s;
        break;
      case "critic":
        call = null;
        blocks.push({ type: "review", step: e.step_idx, round: p.round ?? 0, verdict: p.verdict ?? "ok", review: p.review ?? "" });
        break;
      case "crash":
        blocks.push({ type: "crash", step: e.step_idx, traceback: p.traceback ?? "" });
        break;
    }
  }
  return blocks;
}

function Signature({ tool, args }: { tool: string; args: Record<string, unknown> }) {
  const list = argList(args);
  return (
    <div className="call-sig">
      <span className="fn">{tool}</span>(
      {list.map(([k, v], i) => (
        <span key={k}>
          <span className="arg">{k}=</span>
          {v}
          {i < list.length - 1 ? ", " : ""}
        </span>
      ))}
      )
    </div>
  );
}

function Kind({ label, role }: { label: string; role?: string }) {
  return (
    <div className="trace-kind">
      {label}
      {role && role !== "agent" && <span className="role">{role}</span>}
    </div>
  );
}

function CallRow({ block, live, n }: { block: Extract<Block, { type: "call" }>; live: boolean; n: number }) {
  const meta = useMeta();
  const faulted = block.attempts.some((a) => a.fault);
  const last = block.attempts[block.attempts.length - 1];
  return (
    <div className={`trace-row${faulted ? " faulted" : ""}`}>
      <div className="trace-step">{n}</div>
      <Kind label="tool" role={block.role} />
      <div className="trace-main">
        <Signature tool={block.tool} args={block.args} />
        {block.attempts.map((a, i) => {
          const info = a.fault ? meta.fault_types[a.fault] : undefined;
          return (
            <div className="attempt" key={i}>
              {block.attempts.length > 1 && <span className="muted mono">#{i + 1}</span>}
              <HttpStatus status={a.status} />
              <span className="lat">{ms(a.latency)}</span>
              {a.fault && (
                <>
                  <FaultChip fault={a.fault} detail={info?.description} />
                  {info && (
                    <span className="secondary" style={{ fontSize: 12 }}>
                      {info.layer}, {info.phase === "request" ? "never reached the toolserver" : "rewrote the reply"}
                    </span>
                  )}
                </>
              )}
              {a.retryDelay !== undefined && (
                <span className="secondary" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                  <Retry size={12} /> client retried after {a.retryDelay}s
                </span>
              )}
            </div>
          );
        })}
        {last ? (
          <JsonBody text={last.body} />
        ) : live ? (
          <div className="attempt secondary">
            <span className="spinner" /> waiting for the response
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function Trace({ events, live = false }: { events: TraceEvent[]; live?: boolean }) {
  const blocks = buildBlocks(events);
  if (!blocks.length) {
    return live ? (
      <div className="empty">
        <span className="spinner" style={{ display: "inline-block", verticalAlign: "middle", marginRight: 8 }} />
        resetting the world
      </div>
    ) : (
      <div className="empty">No events recorded.</div>
    );
  }
  return (
    <div className="trace">
      {blocks.map((b, i) => {
        if (b.type === "call") return <CallRow key={i} n={i + 1} block={b} live={live && i === blocks.length - 1} />;
        if (b.type === "model")
          return (
            <div className="trace-row" key={i}>
              <div className="trace-step">{i + 1}</div>
              <Kind label="model" role={b.role} />
              <div className="trace-main">
                {b.text && <div className="model-text">{b.text}</div>}
                {b.calls.length > 0 && (
                  <div className="secondary" style={{ fontSize: 12.5 }}>
                    calls {b.calls.map((c) => c.name).join(", ")}
                  </div>
                )}
                {!b.text && !b.calls.length && <div className="secondary">(empty reply)</div>}
                <div className="muted mono" style={{ fontSize: 11.5 }}>
                  {b.tokens} tokens{b.finish && b.finish !== "STOP" ? ` · finish ${b.finish}` : ""}
                </div>
              </div>
            </div>
          );
        if (b.type === "review")
          return (
            <div className="trace-row" key={i}>
              <div className="trace-step">{i + 1}</div>
              <Kind label="verdict" role={`review ${b.round + 1}`} />
              <div className="trace-main">
                <div className="verdict-line">
                  <span className={`status ${b.verdict === "revise" ? "serious" : "good"}`}>
                    <Retry size={13} />
                    {b.verdict === "revise" ? "sent back for revision" : "accepted"}
                  </span>
                </div>
                {b.review && <div className="model-text secondary">{b.review}</div>}
              </div>
            </div>
          );
        return (
          <div className="trace-row crash" key={i}>
            <div className="trace-step">{i + 1}</div>
            <Kind label="crash" />
            <div className="trace-main">
              <pre>{b.traceback}</pre>
            </div>
          </div>
        );
      })}
    </div>
  );
}
