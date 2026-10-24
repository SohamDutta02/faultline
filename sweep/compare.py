"""Compare two sweeps: what changed in the harnesses, and what that did.

The regression report for harness changes. Run the same matrix before and
after editing a prompt, a retry policy or a check, then:

  python -m sweep.compare --base before --head after

Cells are paired across the sweeps (same task, fault config and repeat get the
same fault seed in every sweep), so a delta is the change's effect on the
same fault schedule.
"""

import argparse
from collections import defaultdict
from pathlib import Path

from harness import changed_parts

from . import store
from .report import render


def _graded(conn, sweep_id: str) -> list[dict]:
    rows = conn.execute("select * from trial where sweep_id = ? and outcome != 'fail_harness'", (sweep_id,))
    return [dict(r) for r in rows]


def _dominant(trials: list[dict]) -> str | None:
    counts = defaultdict(int)
    for t in trials:
        counts[t.get("harness_fp")] += 1
    return max(counts, key=counts.get) if counts else None


def compare(conn, base_label: str, head_label: str, threshold: float = 10.0) -> dict:
    base, head = store.find_sweep(conn, base_label), store.find_sweep(conn, head_label)
    if base is None or head is None:
        missing = base_label if base is None else head_label
        raise KeyError(f"no sweep labelled {missing}")
    b, h = _graded(conn, base["id"]), _graded(conn, head["id"])

    def index(trials):
        out = defaultdict(dict)
        for t in trials:
            out[t["architecture_id"]][(t["task_id"], t["fault_config"], t["repeat_idx"])] = t
        return out

    bi, hi = index(b), index(h)
    both = sorted(set(bi) & set(hi))
    configs = sorted({k[1] for a in both for k in set(bi[a]) & set(hi[a])})

    archs, cells = [], []
    for a in both:
        paired = sorted(set(bi[a]) & set(hi[a]))
        if not paired:
            continue

        def delta(keys):
            wins = sum((hi[a][k]["outcome"] == "pass") - (bi[a][k]["outcome"] == "pass") for k in keys)
            return 100 * wins / len(keys)

        def rate(idx, keys):
            return 100 * sum(idx[a][k]["outcome"] == "pass" for k in keys) / len(keys)

        fp_b, fp_h = _dominant(list(bi[a].values())), _dominant(list(hi[a].values()))
        changed = []
        if fp_b and fp_h and fp_b != fp_h:
            old, new = store.get_harness(conn, fp_b), store.get_harness(conn, fp_h)
            changed = changed_parts(old["manifest"], new["manifest"]) if old and new else ["unknown"]
        archs.append({
            "arch": a, "pairs": len(paired), "base_rate": rate(bi, paired), "head_rate": rate(hi, paired),
            "delta": delta(paired), "base_fp": fp_b, "head_fp": fp_h,
            "harness": "same" if fp_b == fp_h and fp_b else ("unknown" if not (fp_b and fp_h) else "changed"),
            "changed": changed,
        })
        for c in configs:
            keys = [k for k in paired if k[1] == c]
            if keys:
                cells.append({"arch": a, "config": c, "pairs": len(keys), "base_rate": rate(bi, keys),
                              "head_rate": rate(hi, keys), "delta": delta(keys)})

    return {
        "base": {"label": base_label, "model": base["model_id"]},
        "head": {"label": head_label, "model": head["model_id"]},
        "threshold": threshold,
        "archs": archs,
        "cells": cells,
        "regressions": sorted([c for c in cells if c["delta"] <= -threshold], key=lambda c: c["delta"]),
        "improvements": sorted([c for c in cells if c["delta"] >= threshold], key=lambda c: -c["delta"]),
        "only_base": sorted(set(bi) - set(hi)),
        "only_head": sorted(set(hi) - set(bi)),
    }


def build(conn, base_label: str, head_label: str, threshold: float = 10.0, markdown: bool = False) -> str:
    try:
        c = compare(conn, base_label, head_label, threshold)
    except KeyError as exc:
        return str(exc.args[0])
    h = "### " if markdown else ""
    out = [f"{'# ' if markdown else ''}compare {base_label} -> {head_label}",
           f"model {c['base']['model']} -> {c['head']['model']}"
           + ("" if c["base"]["model"] == c["head"]["model"] else "  (the model changed: deltas aren't all harness)")]
    if not c["archs"]:
        return "\n".join(out + ["no architecture has paired cells in both sweeps"])

    rows = [[a["arch"], a["pairs"], f"{a['base_rate']:.0f}%", f"{a['head_rate']:.0f}%", f"{a['delta']:+.0f}",
             a["harness"] + (f" ({', '.join(a['changed'])})" if a["changed"] else "")] for a in c["archs"]]
    out += ["", f"{h}Pass rate on paired cells", render(["arch", "pairs", "base", "head", "pp", "harness"], rows,
                                                         markdown)]
    for title, key in (("Regressions", "regressions"), ("Improvements", "improvements")):
        items = c[key]
        out += ["", f"{h}{title} (|change| >= {threshold:.0f}pp)"]
        if items:
            out.append(render(["arch", "config", "pairs", "base", "head", "pp"], [
                [x["arch"], x["config"], x["pairs"], f"{x['base_rate']:.0f}%", f"{x['head_rate']:.0f}%",
                 f"{x['delta']:+.0f}"] for x in items], markdown))
        else:
            out.append("none")
    if c["only_base"] or c["only_head"]:
        out += ["", f"only in {base_label}: {', '.join(c['only_base']) or '-'}; "
                    f"only in {head_label}: {', '.join(c['only_head']) or '-'}"]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="compare two sweeps cell by cell")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--threshold", type=float, default=10.0, help="percentage points that count as a change")
    ap.add_argument("--markdown", metavar="PATH")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    conn = store.connect(args.db)
    print(build(conn, args.base, args.head, args.threshold))
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(build(conn, args.base, args.head, args.threshold, markdown=True) + "\n")
        print(f"\nwrote {args.markdown}")


if __name__ == "__main__":
    main()
