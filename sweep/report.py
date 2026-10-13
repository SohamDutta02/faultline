import argparse
from collections import defaultdict
from pathlib import Path

from agents.registry import ARCHITECTURES

from . import store
from .run import FAULT_CONFIGS

OUTCOMES = ["pass", "fail_wrong_answer", "fail_no_answer", "fail_loop", "fail_crash"]
COST_FIELDS = ["tool_calls", "tool_errors", "retries", "llm_calls", "tokens_used", "duration_ms"]


def _order(values, preferred):
    rank = {v: i for i, v in enumerate(preferred)}
    return sorted(set(values), key=lambda v: (rank.get(v, len(rank)), v))


def summarize(conn, label: str) -> dict | None:
    """Everything the text report and the web UI show about one sweep."""
    sweep = store.find_sweep(conn, label)
    if not sweep:
        return None
    trials = conn.execute("select * from trial where sweep_id = ?", (sweep["id"],)).fetchall()
    graded = [t for t in trials if t["outcome"] != "fail_harness"]

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
        "cells": [{"config": c, "arch": a, **tally(groups[(c, a)])} for c in configs for a in archs],
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
