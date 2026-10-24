"""Controlled comparisons and harness spread over a sweep's trials.

Controlled comparisons: every pair of harnesses in the sweep that differ in
exactly one subsystem (same model, or both scripted) is an exclusion test for
that subsystem. Trials are paired by cell (task, fault config, repeat), and
every harness in a cell faced the same fault schedule, so the difference is
the subsystem's marginal effect in that context. An --ablate sweep produces
these pairs in two contexts, the baseline (add-one) and the full harness
(leave-one-out); when the same change moves the pass rate by very different
amounts in different contexts, subsystems interact and a single ablation
number would mislead.

Harness spread: at a fixed model, how far apart the best and the worst harness
are under each fault config: how much of the outcome the harness decides.
"""

from collections import defaultdict
from itertools import combinations

from agents.registry import BadHarness, resolve
from harness import BASELINE, OPTIONS

INTERACTION_PP = 10  # contexts disagreeing by at least this much (overall or under one fault) interact


def _arch(a: str):
    try:
        return resolve(a)
    except BadHarness:
        return None


def _orient(slot: str, a, b):
    """(from, to): from is the baseline option when one side has it, else the earlier-listed option."""
    va, vb = getattr(a.spec, slot), getattr(b.spec, slot)
    order = list(OPTIONS[slot])
    if vb == getattr(BASELINE, slot) or (va != getattr(BASELINE, slot) and order.index(vb) < order.index(va)):
        return b, a
    return a, b


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def comparisons(trials: list, configs: list[str]) -> list[dict]:
    by_arch_cell: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for t in trials:
        by_arch_cell[t["architecture_id"]][(t["task_id"], t["fault_config"], t["repeat_idx"])] = t
    archs = {a: arch for a in by_arch_cell if (arch := _arch(a)) is not None}

    out = []
    done = set()
    # shorter names first, so a pair spelled two ways is reported under its plainest names
    for a_id, b_id in combinations(sorted(archs, key=lambda x: (len(x), x)), 2):
        a, b = archs[a_id], archs[b_id]
        if a.policy != b.policy:
            continue
        key = frozenset([(a.policy, a.spec), (b.policy, b.spec)])
        if key in done:
            continue
        diff = a.spec.differs(b.spec)
        if len(diff) != 1:
            continue
        slot = diff[0]
        lo, hi = _orient(slot, a, b)
        cells_lo, cells_hi = by_arch_cell[lo.id], by_arch_cell[hi.id]
        paired = sorted(set(cells_lo) & set(cells_hi))
        if not paired:
            continue
        done.add(key)

        def deltas(keys, field=None):
            if field is None:
                return [(cells_hi[k]["outcome"] == "pass") - (cells_lo[k]["outcome"] == "pass") for k in keys]
            return [(cells_hi[k][field] or 0) - (cells_lo[k][field] or 0) for k in keys]

        by_config = {}
        for c in configs:
            keys = [k for k in paired if k[1] == c]
            if keys:
                by_config[c] = {"delta": 100 * _mean(deltas(keys)), "n": len(keys)}
        out.append({
            "slot": slot,
            "from_option": getattr(lo.spec, slot),
            "to_option": getattr(hi.spec, slot),
            "from_arch": lo.id,
            "to_arch": hi.id,
            "n": len(paired),
            "delta": 100 * _mean(deltas(paired)),
            "d_tokens": _mean(deltas(paired, "tokens_used")),
            "d_tool_calls": _mean(deltas(paired, "tool_calls")),
            "d_llm_calls": _mean(deltas(paired, "llm_calls")),
            "by_config": by_config,
        })
    out.sort(key=lambda r: (list(OPTIONS).index(r["slot"]), r["from_option"], r["to_option"], r["from_arch"]))
    return out


def interactions(rows: list[dict], threshold: float = INTERACTION_PP) -> list[dict]:
    """The same subsystem change measured in several contexts, where the effects
    disagree: overall ("all"), or under a single fault config, where most
    effects live."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["slot"], r["from_option"], r["to_option"])].append(r)
    out = []
    for (slot, frm, to), rs in groups.items():
        if len(rs) < 2:
            continue
        configs = ["all"] + sorted({c for r in rs for c in r["by_config"]})
        for config in configs:
            measured = [(r, r["delta"] if config == "all" else r["by_config"][config]["delta"]) for r in rs
                        if config == "all" or config in r["by_config"]]
            if len(measured) < 2:
                continue
            gap = max(d for _, d in measured) - min(d for _, d in measured)
            if gap >= threshold:
                out.append({
                    "slot": slot, "from_option": frm, "to_option": to, "config": config, "gap": gap,
                    "contexts": [{"from_arch": r["from_arch"], "to_arch": r["to_arch"], "delta": d}
                                 for r, d in sorted(measured, key=lambda x: -x[1])],
                })
    return out


def spread(cells: list[dict], configs: list[str]) -> list[dict]:
    """Best and worst LLM harness per fault config, and overall. cells: report cells with passes/n."""
    llm = {c["arch"] for c in cells if (arch := _arch(c["arch"])) is not None and arch.needs_llm}
    out = []

    def row(config, tallies):
        rates = {a: p / n for a, (p, n) in tallies.items() if n}
        if len(rates) < 2:
            return None
        best = max(rates, key=lambda a: (rates[a], a))
        worst = min(rates, key=lambda a: (rates[a], a))
        return {"config": config, "best": best, "best_rate": 100 * rates[best], "worst": worst,
                "worst_rate": 100 * rates[worst], "spread": 100 * (rates[best] - rates[worst]), "harnesses": len(rates)}

    totals = defaultdict(lambda: [0, 0])
    for c in configs:
        tallies = {}
        for cell in cells:
            if cell["config"] == c and cell["arch"] in llm:
                tallies[cell["arch"]] = (cell["passes"], cell["n"])
                totals[cell["arch"]][0] += cell["passes"]
                totals[cell["arch"]][1] += cell["n"]
        if (r := row(c, tallies)) is not None:
            out.append(r)
    if out and (r := row("all", {a: tuple(v) for a, v in totals.items()})) is not None:
        out.append(r)
    return out
