import argparse
import json
from collections import defaultdict
from pathlib import Path

from agents.registry import ARCHITECTURES

from . import analysis, attribution, store
from .run import FAULT_CONFIGS

OUTCOMES = ["pass", "fail_wrong_answer", "fail_no_answer", "fail_loop", "fail_crash"]
COST_FIELDS = ["tool_calls", "tool_errors", "retries", "llm_calls", "tokens_used", "duration_ms"]


def _order(values, preferred):
    rank = {v: i for i, v in enumerate(preferred)}
    return sorted(set(values), key=lambda v: (rank.get(v, len(rank)), v))


def feedback_stats(rows) -> dict:
    """What feedback did, from trials that recorded their first answer's grade.

    fixed: first answer failed, final passed. broke: the reverse. flagged: the
    first review asked for a revision; a false alarm is a flag on a first
    answer that had passed, a miss is no flag on one that had failed."""
    s = {"n": 0, "first_pass": 0, "final": 0, "fixed": 0, "broke": 0, "flagged": 0,
         "false_alarms": 0, "missed": 0, "extra_tool_calls": 0.0, "extra_tokens": 0.0}
    for t in rows:
        fb = t["feedback"]
        fb = json.loads(fb) if isinstance(fb, str) else fb
        if not fb:
            continue
        first = fb["first_pass"].get("outcome") == "pass"
        final = t["outcome"] == "pass"
        reviews = fb.get("reviews") or []
        flagged = bool(reviews) and reviews[0]["verdict"] == "revise"
        s["n"] += 1
        s["first_pass"] += first
        s["final"] += final
        s["fixed"] += (not first) and final
        s["broke"] += first and not final
        s["flagged"] += flagged
        s["false_alarms"] += first and flagged
        s["missed"] += (not first) and not flagged
        s["extra_tool_calls"] += (t["tool_calls"] or 0) - fb["first_pass"].get("tool_calls", 0)
        s["extra_tokens"] += (t["tokens_used"] or 0) - fb["first_pass"].get("tokens", 0)
    if s["n"]:
        s["extra_tool_calls"] /= s["n"]
        s["extra_tokens"] /= s["n"]
    return s


def summarize(conn, label: str) -> dict | None:
    """Everything the text report and the web UI show about one sweep."""
    sweep = store.find_sweep(conn, label)
    if not sweep:
        return None
    trials = conn.execute("select * from trial where sweep_id = ?", (sweep["id"],)).fetchall()
    graded = [dict(t) for t in trials if t["outcome"] != "fail_harness"]

    archs = _order([t["architecture_id"] for t in graded], list(ARCHITECTURES))
    configs = _order([t["fault_config"] for t in graded], FAULT_CONFIGS)
    tasks = _order([t["task_id"] for t in graded], [])

    def tally(rows):
        return {"passes": sum(r["outcome"] == "pass" for r in rows), "n": len(rows)}

    groups = defaultdict(list)
    by_task = defaultdict(list)
    for t in graded:
        groups[(t["fault_config"], t["architecture_id"])].append(t)
        by_task[(t["architecture_id"], t["task_id"], t["fault_config"])].append(t)

    def mean(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else 0.0

    per_arch = {a: [t for t in graded if t["architecture_id"] == a] for a in archs}
    per_cfg = {c: [t for t in graded if t["fault_config"] == c] for c in configs}
    cells = [{"config": c, "arch": a, **tally(groups[(c, a)])} for c in configs for a in archs]
    comparisons = analysis.comparisons(graded, configs)

    return {
        "label": label,
        "model": sweep["model_id"],
        "status": sweep["status"],
        "started_at": sweep["started_at"],
        "repeats": sweep["repeats_per_cell"],
        "graded": len(graded),
        "harness": len(trials) - len(graded),
        "archs": archs,
        "configs": configs,
        "tasks": tasks,
        "cells": cells,
        "fired": [
            {"config": c, "fired": sum(t["fault_fired"] for t in per_cfg[c]), "n": len(per_cfg[c])}
            for c in configs
        ],
        "outcomes": [{"arch": a, **{o: sum(t["outcome"] == o for t in per_arch[a]) for o in OUTCOMES}} for a in archs],
        "cost": [{"arch": a, **{f: mean(per_arch[a], f) for f in COST_FIELDS}} for a in archs],
        "by_task": [
            {"arch": a, "task": tid, "config": c, **tally(by_task[(a, tid, c)])}
            for a in archs for tid in tasks for c in configs
        ],
        "feedback": [
            {"arch": a, **stats} for a in archs if (stats := feedback_stats(per_arch[a]))["n"]
        ],
        "spread": analysis.spread(cells, configs),
        "comparisons": comparisons,
        "interactions": analysis.interactions(comparisons),
        "attribution": [{"arch": a, **attribution.summarize(per_arch[a])} for a in archs],
    }


def render(headers: list[str], rows: list[list], markdown: bool = False) -> str:
    cells = [[str(c) for c in r] for r in rows]
    if markdown:
        out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] + ["---:"] * (len(headers) - 1)) + "|"]
        out += ["| " + " | ".join(r) + " |" for r in cells]
        return "\n".join(out)
    widths = [max(len(x) for x in col) for col in zip(headers, *cells)]

    def line(r):
        return "  ".join(x.ljust(w) if i == 0 else x.rjust(w) for i, (x, w) in enumerate(zip(r, widths)))

    return "\n".join([line(headers)] + [line(r) for r in cells])


def _cell(c: dict) -> str:
    return f"{100 * c['passes'] / c['n']:.0f}% ({c['n']})" if c["n"] else "-"


def build(conn, label: str, by_task: bool = False, markdown: bool = False) -> str:
    s = summarize(conn, label)
    if s is None:
        return f"no sweep labelled {label}"
    if not s["graded"]:
        return f"sweep {label}: no graded trials yet ({s['harness']} harness failures)"

    archs, cfgs = s["archs"], s["configs"]
    grid = {(c["config"], c["arch"]): c for c in s["cells"]}
    h = "### " if markdown else ""
    out = [
        f"{'# ' if markdown else ''}sweep {label}",
        f"model {s['model']}, status {s['status']}, {s['graded']} graded trials"
        + (f" ({s['harness']} harness failures excluded)" if s["harness"] else ""),
        "",
        f"{h}Pass rate (n trials)",
        render(["config"] + archs, [[c] + [_cell(grid[(c, a)]) for a in archs] for c in cfgs], markdown),
    ]

    if "none" in cfgs and len(cfgs) > 1:
        rows = []
        for c in cfgs:
            if c == "none":
                continue
            row = [c]
            for a in archs:
                base, cur = grid[("none", a)], grid[(c, a)]
                if base["n"] and cur["n"]:
                    row.append(f"{100 * (cur['passes'] / cur['n'] - base['passes'] / base['n']):+.0f}")
                else:
                    row.append("-")
            rows.append(row)
        out += ["", f"{h}Change vs no-fault baseline (percentage points)", render(["config"] + archs, rows, markdown)]

    if s["spread"]:
        rows = [[r["config"], r["best"], f"{r['best_rate']:.0f}%", r["worst"], f"{r['worst_rate']:.0f}%",
                 f"{r['spread']:.0f}"] for r in s["spread"]]
        out += ["", f"{h}Harness spread: best vs worst harness on the same model",
                render(["config", "best", "", "worst", "", "spread (pp)"], rows, markdown)]

    if s["comparisons"]:
        rows = []
        for i, r in enumerate(s["comparisons"], 1):
            per_1k = f"{r['delta'] / (r['d_tokens'] / 1000):+.1f}" if r["d_tokens"] > 50 else "-"
            rows.append([f"#{i}", r["slot"], f"{r['from_option']} -> {r['to_option']}",
                         f"{r['from_arch']} -> {r['to_arch']}", r["n"], f"{r['delta']:+.0f}",
                         f"{r['d_tokens']:+.0f}", f"{r['d_tool_calls']:+.1f}", per_1k])
        out += ["", f"{h}Controlled comparisons: harnesses that differ in one subsystem, paired by cell",
                render(["", "subsystem", "change", "from -> to", "pairs", "pass (pp)", "tokens", "tool calls",
                        "pp per 1k tokens"], rows, markdown)]
        cfg_rows = [[f"#{i}"] + [f"{r['by_config'][c]['delta']:+.0f}" if c in r["by_config"] else "-" for c in cfgs]
                    for i, r in enumerate(s["comparisons"], 1)]
        out += ["", f"{h}Controlled comparisons by fault config (pp)", render([""] + cfgs, cfg_rows, markdown)]
        for x in s["interactions"]:
            ctx = "; ".join(f"{c['delta']:+.0f}pp from {c['from_arch']} to {c['to_arch']}" for c in x["contexts"])
            where = "overall" if x["config"] == "all" else f"under {x['config']}"
            out.append(f"interaction {where}: {x['slot']} {x['from_option']} -> {x['to_option']} depends on the rest "
                       f"of the harness ({ctx}). Its effect is not one number.")

    fired_rows = [[f["config"], f"{100 * f['fired'] / f['n']:.0f}%", f["n"]] for f in s["fired"]]
    out += ["", f"{h}Trials where the fault fired", render(["config", "fired", "n"], fired_rows, markdown)]
    silent = [f["config"] for f in s["fired"] if f["config"] != "none" and f["fired"] == 0]
    if silent:
        out.append(
            f"warning: {', '.join(silent)} never fired. Either the agent never called the targeted tool, "
            "or its traffic bypassed the proxy."
        )

    outcome_rows = [[o["arch"]] + [o[k] for k in OUTCOMES] for o in s["outcomes"]]
    out += ["", f"{h}Outcomes", render(["arch"] + [o.removeprefix("fail_") for o in OUTCOMES], outcome_rows, markdown)]

    cost_rows = [
        [
            c["arch"], f"{c['tool_calls']:.1f}", f"{c['tool_errors']:.1f}", f"{c['retries']:.1f}",
            f"{c['llm_calls']:.1f}", f"{c['tokens_used']:.0f}", f"{c['duration_ms'] / 1000:.1f}",
        ]
        for c in s["cost"]
    ]
    out += [
        "",
        f"{h}Mean cost per trial",
        render(["arch", "tool calls", "tool errors", "retries", "llm calls", "tokens", "seconds"], cost_rows, markdown),
    ]

    attributed = [a for a in s["attribution"] if a["failures"]]
    if attributed:
        causes = [c for c in attribution.CAUSES if any(a["causes"].get(c) for a in attributed)]
        rows = [[a["arch"], a["failures"]] + [a["causes"].get(c, 0) for c in causes] for a in attributed]
        out += ["", f"{h}Failure attribution: cause", render(["arch", "failures"] + causes, rows, markdown)]
        subs = [x for x in attribution.SUBSYSTEMS + ["none"] if any(a["subsystems"].get(x) for a in attributed)]
        rows = [[a["arch"]] + [f"{100 * a['subsystems'].get(x, 0) / a['failures']:.0f}%" for x in subs]
                for a in attributed]
        out += ["", f"{h}Failure attribution: subsystem that should have prevented it (share of failures)",
                render(["arch"] + subs, rows, markdown)]

    if s["feedback"]:
        def pct(k, f):
            return f"{100 * f[k] / f['n']:.0f}%"
        fb_rows = [
            [f["arch"], f["n"], pct("first_pass", f), pct("final", f), f["fixed"], f["broke"], f["flagged"],
             f["false_alarms"], f["missed"], f"{f['extra_tool_calls']:.1f}", f"{f['extra_tokens']:.0f}"]
            for f in s["feedback"]
        ]
        out += [
            "",
            f"{h}Feedback: first answer vs final",
            render(["arch", "n", "first pass", "final", "fixed", "broke", "flagged", "false alarms", "missed",
                    "+tool calls", "+tokens"], fb_rows, markdown),
        ]

    if by_task:
        tgrid = {(b["arch"], b["task"], b["config"]): b for b in s["by_task"]}
        for a in archs:
            rows = [[tid] + [_cell(tgrid[(a, tid, c)]) for c in cfgs] for tid in s["tasks"]]
            out += ["", f"{h}Pass rate by task: {a}", render(["task"] + cfgs, rows, markdown)]

    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="summarise a sweep")
    ap.add_argument("--label", required=True)
    ap.add_argument("--by-task", action="store_true", help="add a task x config grid per architecture")
    ap.add_argument("--markdown", metavar="PATH", help="also write the report as markdown")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    conn = store.connect(args.db)
    print(build(conn, args.label, args.by_task))
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(build(conn, args.label, args.by_task, markdown=True) + "\n", encoding="utf-8")
        print(f"\nwrote {args.markdown}")


if __name__ == "__main__":
    main()
