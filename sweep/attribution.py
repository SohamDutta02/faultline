"""Why did a trial fail, and which harness subsystem should have prevented it?

Ablations say how much a subsystem moves the pass rate; they don't say where
the remaining failures come from. This reads one failed trial's record (its
trace, the world diff, the feedback checkpoint) and names a cause, the
subsystem whose job it was to prevent it, and the evidence. The experimenter
can see injected faults, so unlike the harness's own checks this uses them.

Causes, in the order they're tested:

  crash               the harness raised                         (no subsystem: a bug)
  feedback_broke_it   the first answer passed, the final didn't  feedback
  loop                step budget exhausted                      state
  duplicate_write     a write went through more than once        state (after an ambiguous error)
                                                                 or instructions (no error)
  lost_write          a write errored and never went through     tools
  trusted_bad_data    a read was altered and nothing caught it   feedback
  gave_up_on_errors   transport/schema errors, never recovered   tools
  misread_write       a write reported failure but went through  state
  reasoning_error     wrong with no unrecovered fault            instructions (or the model)
  unattributed        none of the above
"""

import argparse
import json
import re
from collections import Counter

from agents.toolbox import WRITES
from harness.checks import agent_calls

from . import store

SEMANTIC = {"stale_data", "plausible_wrong"}
TRANSPORT = {"rate_limit", "timeout", "malformed_json"}
CAUSES = ["crash", "feedback_broke_it", "loop", "duplicate_write", "lost_write", "trusted_bad_data",
          "gave_up_on_errors", "misread_write", "reasoning_error", "unattributed"]
SUBSYSTEMS = ["instructions", "tools", "environment", "state", "feedback"]


def _fmt(tool: str, args: dict) -> str:
    return f"{tool}(" + ", ".join(f"{k}={args[k]}" for k in sorted(args)) + ")"


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])-?\d+(?:\.\d+)?(?![\w-])", text or ""))


def _get(e, key, default=None):
    return e.get(key, default) if isinstance(e, dict) else getattr(e, key, default)


def _result(cause: str, subsystem: str | None, evidence: str, also: list[str] | None = None, notes=None) -> dict:
    return {"cause": cause, "subsystem": subsystem, "evidence": evidence[:400], "also": also or [],
            "notes": notes or []}


def attribute(trial: dict) -> dict | None:
    """trial: a stored trial (store.get_trial) or a TrialResult as a dict."""
    outcome = trial["outcome"]
    if outcome in ("pass", "fail_harness"):
        return None
    reason = trial.get("reason") or ""
    events = trial.get("events") or []
    fb = trial.get("feedback")
    fb = json.loads(fb) if isinstance(fb, str) else fb
    diff = trial.get("world_diff") or {}
    diff = json.loads(diff) if isinstance(diff, str) else diff

    notes = []
    if fb and fb.get("reviews") and fb["first_pass"].get("outcome") != "pass" and fb["reviews"][0]["verdict"] == "ok":
        notes.append(f"{fb['option']} feedback approved the failing first answer")

    def done(cause, subsystem, evidence, also=None):
        return _result(cause, subsystem, evidence, also, notes)

    if outcome == "fail_crash":
        return done("crash", None, reason)
    if fb and fb["first_pass"].get("outcome") == "pass":
        rounds = len(fb.get("reviews") or [])
        return done("feedback_broke_it", "feedback",
                    f"the first answer passed; after {rounds} {fb['option']} review round(s) it failed: {reason}")
    if outcome == "fail_loop":
        return done("loop", "state", f"step budget exhausted ({reason})", ["instructions"])

    calls = agent_calls(events)
    agent_results = [e for e in events
                     if _get(e, "kind") == "tool_result" and (_get(e, "channel") or "agent") == "agent"]
    fired = [(_get(e, "tool_name"), (_get(e, "payload") or {}).get("fault"), e) for e in agent_results
             if _get(e, "fault_injected")]
    kinds = {f for _, f, _ in fired}

    # duplicate writes: more than one new order for the same (customer, sku, qty)
    added = Counter((o["customer_id"], o["sku"], o["qty"]) for o in diff.get("orders_added", []))
    dups = [k for k, n in added.items() if n > 1]
    if dups:
        cust, sku, qty = dups[0]
        call = _fmt("place_order", {"customer_id": cust, "sku": sku, "qty": qty})
        ids = [o["order_id"] for o in diff["orders_added"] if (o["customer_id"], o["sku"], o["qty"]) == dups[0]]
        ambiguous = [c for c in calls if c.tool == "place_order" and c.args.get("sku") == sku
                     and (c.status is None or c.status >= 500 or (c.status < 300 and not c.ok))]
        if ambiguous:
            return done("duplicate_write", "state",
                        f"{call} returned {ambiguous[0].status} but went through, then was placed again "
                        f"({', '.join(ids)})", ["environment"])
        return done("duplicate_write", "instructions", f"{call} was placed {len(ids)} times with no error "
                    f"in between ({', '.join(ids)})")

    # lost writes: a write errored and never went through
    changed = {o["order_id"] for o in diff.get("orders_changed", [])}
    for c in calls:
        if c.tool not in WRITES or c.ok:
            continue
        if any(o.ok and o.tool == c.tool and o.args == c.args for o in calls):
            continue
        if c.tool == "place_order":
            landed = any((o["customer_id"], o["sku"], o["qty"]) ==
                         (c.args.get("customer_id"), c.args.get("sku"), c.args.get("qty"))
                         for o in diff.get("orders_added", []))
        else:
            landed = c.args.get("order_id") in changed
        if not landed and (c.status is None or c.status == 429 or c.status >= 500):
            return done("lost_write", "tools", f"{_fmt(c.tool, c.args)} returned {c.status} and never went through",
                        ["environment"])

    semantic = [(tool, f, e) for tool, f, e in fired if f in SEMANTIC]
    if semantic:
        tool, fault, e = semantic[0]
        body = (_get(e, "payload") or {}).get("body", "")
        used = _numbers(body) & _numbers(trial.get("answer") or "")
        evidence = f"{tool} returned altered data ({fault}) and nothing caught it"
        if used:
            evidence += f"; the answer uses {', '.join(sorted(used))} from the altered response"
        return done("trusted_bad_data", "feedback", evidence)

    unrecovered = [(tool, f) for tool, f, e in fired if f in TRANSPORT]
    if unrecovered:
        # a faulted call counts as recovered if the same call later succeeded
        failed_calls = [c for c in calls if c.failed and not any(
            o.ok and o.tool == c.tool and o.args == c.args and o.idx > c.idx for o in calls)]
        if failed_calls or outcome == "fail_no_answer":
            names = sorted({f for _, f in unrecovered})
            return done("gave_up_on_errors", "tools",
                        f"{len(unrecovered)} injected {'/'.join(names)} error(s); "
                        f"{len(failed_calls)} call(s) never succeeded", ["instructions"])

    if "partial_write" in kinds:
        return done("misread_write", "state", f"a write reported failure but went through: {reason}",
                    ["environment"])

    return done("reasoning_error", "instructions",
                f"wrong with no unrecovered fault: {reason}" if fired else f"wrong with clean data: {reason}",
                ["model"])


def summarize(rows: list[dict]) -> dict:
    """Counts by cause and by subsystem, from trials with an attribution."""
    causes = Counter()
    subsystems = Counter()
    for r in rows:
        a = r.get("attribution")
        a = json.loads(a) if isinstance(a, str) else a
        if not a:
            continue
        causes[a["cause"]] += 1
        subsystems[a["subsystem"] or "none"] += 1
    return {"failures": sum(causes.values()), "causes": dict(causes), "subsystems": dict(subsystems)}


def backfill(conn, label: str) -> int:
    """Recompute the attribution of every trial in a sweep; returns how many failed trials got one."""
    sweep = store.find_sweep(conn, label)
    if not sweep:
        raise SystemExit(f"no sweep labelled {label}")
    ids = [r["id"] for r in conn.execute("select id from trial where sweep_id = ?", (sweep["id"],))]
    n = 0
    for tid in ids:
        a = attribute(store.get_trial(conn, tid))
        conn.execute("update trial set attribution = ? where id = ?", (json.dumps(a) if a else None, tid))
        n += a is not None
    conn.commit()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="(re)compute failure attributions for a sweep")
    ap.add_argument("--label", required=True)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    conn = store.connect(args.db)
    print(f"attributed {backfill(conn, args.label)} failed trials in {args.label}")


if __name__ == "__main__":
    main()
