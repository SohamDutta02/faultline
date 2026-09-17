import argparse
import os
import time

import httpx

from agents.llm import MODEL
from tasks.catalog import CATALOG
from runner.trial import run_trial

from . import store

PROXY = os.getenv("TOOLSERVER_URL", "http://127.0.0.1:8200")

FAULT_CONFIGS = ["none", "rate_limit", "stale_data", "plausible_wrong"]
ARCHITECTURES = ["single"]


def set_fault_config(name: str, seed: int):
    with httpx.Client(timeout=10) as c:
        r = c.post(f"{PROXY}/_proxy/config", params={"name": name, "seed": seed})
        r.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="sweep-001")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--pace", type=float, default=15.0)
    ap.add_argument("--configs", nargs="*", default=FAULT_CONFIGS)
    args = ap.parse_args()

    conn = store.connect()
    sweep_id = store.start_sweep(conn, args.label, MODEL, args.repeats)
    done = store.done_cells(conn, sweep_id)

    cells = [
        (task, arch, cfg, rep)
        for cfg in args.configs
        for task in CATALOG
        for arch in ARCHITECTURES
        for rep in range(args.repeats)
    ]
    todo = [c for c in cells if (c[0].id, c[1], c[2], c[3]) not in done]

    print(f"sweep {args.label}: {len(todo)} trials to run ({len(done)} already done)")

    for i, (task, arch, cfg, rep) in enumerate(todo, 1):
        seed = 1000 * rep + task.seed
        try:
            set_fault_config(cfg, seed)
        except Exception as exc:
            print(f"  proxy config failed: {exc}")
            break

        result = run_trial(task, architecture_id=arch)
        store.record(conn, sweep_id, cfg, rep, seed, result)

        flag = "F" if result.fault_fired else " "
        print(f"[{i}/{len(todo)}] {cfg:16} {task.id:18} r{rep} {flag} {result.outcome}")

        if "fail_crash" in result.outcome and "PerDay" in (result.reason or ""):
            print("  daily quota exhausted — stopping. rerun with same --label to resume.")
            break

        time.sleep(args.pace)

    store.finish_sweep(conn, sweep_id)
    print("done")


if __name__ == "__main__":
    main()