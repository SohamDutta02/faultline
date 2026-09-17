import argparse

from . import store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="sweep-001")
    args = ap.parse_args()

    conn = store.connect()
    row = conn.execute("select id from sweep where label = ?", (args.label,)).fetchone()
    if not row:
        print(f"no sweep labelled {args.label}")
        return
    sweep_id = row["id"]

    rows = conn.execute(
        """select fault_config, architecture_id,
                  sum(outcome = 'pass') as passes,
                  count(*) as n
           from trial
           where sweep_id = ? and outcome != 'fail_harness'
           group by fault_config, architecture_id""",
        (sweep_id,),
    ).fetchall()

    if not rows:
        print("no trials recorded")
        return

    archs = sorted({r["architecture_id"] for r in rows})
    cfgs = sorted({r["fault_config"] for r in rows}, key=lambda c: (c != "none", c))
    grid = {(r["fault_config"], r["architecture_id"]): (r["passes"], r["n"]) for r in rows}

    width = max(len(c) for c in cfgs) + 2
    print("\n" + " " * width + "".join(f"{a:>14}" for a in archs))
    for c in cfgs:
        cells = []
        for a in archs:
            p, n = grid.get((c, a), (0, 0))
            cells.append(f"{100*p/n:>9.0f}% ({n})" if n else f"{'-':>14}")
        print(f"{c:<{width}}" + "".join(cells))

    print()
    breakdown = conn.execute(
        """select fault_config, outcome, count(*) as n
           from trial where sweep_id = ? group by fault_config, outcome
           order by fault_config, n desc""",
        (sweep_id,),
    ).fetchall()
    for r in breakdown:
        print(f"{r['fault_config']:<18} {r['outcome']:<20} {r['n']}")


if __name__ == "__main__":
    main()