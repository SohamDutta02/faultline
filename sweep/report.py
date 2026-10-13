import argparse
from collections import defaultdict
from pathlib import Path

from agents.registry import ARCHITECTURES

from . import store
from .run import FAULT_CONFIGS

OUTCOMES = ["pass", "fail_wrong_answer", "fail_no_answer", "fail_loop", "fail_crash"]


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


def _order(values, preferred):
    rank = {v: i for i, v in enumerate(preferred)}
    return sorted(set(values), key=lambda v: (rank.get(v, len(rank)), v))


def _rate(p: int, n: int) -> float | None:
    return p / n if n else None


def _cell(p: int, n: int) -> str:
    return f"{100 * p / n:.0f}% ({n})" if n else "-"


def build(conn, label: str, by_task: bool = False, markdown: bool = False) -> str:
    sweep = store.find_sweep(conn, label)
    if not sweep:
        return f"no sweep labelled {label}"
    trials = conn.execute("select * from trial where sweep_id = ?", (sweep["id"],)).fetchall()
    graded = [t for t in trials if t["outcome"] != "fail_harness"]
    harness = len(trials) - len(graded)
    if not graded:
        return f"sweep {label}: no graded trials yet ({harness} harness failures)"

    archs = _order([t["architecture_id"] for t in graded], list(ARCHITECTURES))
    cfgs = _order([t["fault_config"] for t in graded], FAULT_CONFIGS)
    tasks = _order([t["task_id"] for t in graded], [])

    def tally(rows):
        return sum(r["outcome"] == "pass" for r in rows), len(rows)

    by_cfg_arch = defaultdict(list)
    for t in graded:
        by_cfg_arch[(t["fault_config"], t["architecture_id"])].append(t)

    h = "### " if markdown else ""
    out = [
        f"{'# ' if markdown else ''}sweep {label}",
        f"model {sweep['model_id']}, status {sweep['status']}, {len(graded)} graded trials"
        + (f" ({harness} harness failures excluded)" if harness else ""),
        "",
        f"{h}Pass rate (n trials)",
        render(
            ["config"] + archs,
            [[c] + [_cell(*tally(by_cfg_arch[(c, a)])) for a in archs] for c in cfgs],
            markdown,
        ),
    ]

    if "none" in cfgs and len(cfgs) > 1:
        rows = []
        for c in cfgs:
            if c == "none":
                continue
            row = [c]
            for a in archs:
                base = _rate(*tally(by_cfg_arch[("none", a)]))
                cur = _rate(*tally(by_cfg_arch[(c, a)]))
                row.append("-" if base is None or cur is None else f"{100 * (cur - base):+.0f}")
            rows.append(row)
        out += ["", f"{h}Change vs no-fault baseline (percentage points)", render(["config"] + archs, rows, markdown)]

    fired_rows, silent = [], []
    for c in cfgs:
        rows = [t for t in graded if t["fault_config"] == c]
        fired = sum(t["fault_fired"] for t in rows)
        fired_rows.append([c, f"{100 * fired / len(rows):.0f}%", len(rows)])
        if c != "none" and fired == 0:
            silent.append(c)
    out += ["", f"{h}Trials where the fault fired", render(["config", "fired", "n"], fired_rows, markdown)]
    if silent:
        out.append(
            f"warning: {', '.join(silent)} never fired. Either the agent never called the targeted tool, "
            "or its traffic bypassed the proxy."
        )

    outcome_rows = []
    for a in archs:
        rows = [t for t in graded if t["architecture_id"] == a]
        outcome_rows.append([a] + [sum(t["outcome"] == o for t in rows) for o in OUTCOMES])
    out += ["", f"{h}Outcomes", render(["arch"] + [o.removeprefix("fail_") for o in OUTCOMES], outcome_rows, markdown)]

    def mean(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else 0

    cost_rows = []
    for a in archs:
        rows = [t for t in graded if t["architecture_id"] == a]
        cost_rows.append([
            a,
            f"{mean(rows, 'tool_calls'):.1f}",
            f"{mean(rows, 'tool_errors'):.1f}",
            f"{mean(rows, 'retries'):.1f}",
            f"{mean(rows, 'llm_calls'):.1f}",
            f"{mean(rows, 'tokens_used'):.0f}",
            f"{mean(rows, 'duration_ms') / 1000:.1f}",
        ])
    out += [
        "",
        f"{h}Mean cost per trial",
        render(["arch", "tool calls", "tool errors", "retries", "llm calls", "tokens", "seconds"], cost_rows, markdown),
    ]

    if by_task:
        for a in archs:
            grid = defaultdict(list)
            for t in graded:
                if t["architecture_id"] == a:
                    grid[(t["task_id"], t["fault_config"])].append(t)
            rows = [[tid] + [_cell(*tally(grid[(tid, c)])) for c in cfgs] for tid in tasks]
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
