import argparse
import time
import zlib
from contextlib import nullcontext

import httpx

import settings
from agents.llm import LLMError
from agents.registry import ARCHITECTURES
from proxy.config import available
from runner.stack import local_stack
from runner.trial import run_trial
from tasks.catalog import BY_ID, CATALOG

from . import store

FAULT_CONFIGS = ["none", "rate_limit", "timeout", "malformed_json", "stale_data", "plausible_wrong", "partial_write"]
LLM_ARCHITECTURES = [a for a, spec in ARCHITECTURES.items() if spec.needs_llm]


def cell_seed(task_id: str, fault_config: str, repeat_idx: int) -> int:
    # Every architecture gets the same seed for a (task, config, repeat) cell, so
    # they face the same fault schedule and can be compared pairwise.
    return zlib.crc32(f"{task_id}|{fault_config}|{repeat_idx}".encode()) % 1_000_000


def set_fault_config(proxy_url: str, name: str, seed: int):
    r = httpx.post(f"{proxy_url}/_proxy/config", params={"name": name, "seed": seed}, timeout=10)
    r.raise_for_status()


def preflight(toolserver_url: str, proxy_url: str):
    try:
        httpx.get(f"{toolserver_url}/health", timeout=5).raise_for_status()
    except Exception as exc:
        raise SystemExit(f"toolserver not reachable at {toolserver_url} ({exc}); start it or pass --serve")
    try:
        status = httpx.get(f"{proxy_url}/_proxy/status", timeout=5)
        status.raise_for_status()
    except Exception as exc:
        raise SystemExit(
            f"no fault proxy at {proxy_url} ({exc}); agent traffic must go through the proxy. "
            "Start it, set PROXY_URL, or pass --serve"
        )
    try:
        httpx.get(f"{proxy_url}/health", timeout=5).raise_for_status()
    except Exception as exc:
        raise SystemExit(f"proxy cannot reach its upstream {status.json()['upstream']} ({exc})")


def build_parser():
    ap = argparse.ArgumentParser(description="run every (task, architecture, fault config, repeat) cell")
    ap.add_argument("--label", required=True, help="sweep name; rerun with the same label to resume")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--pace", type=float, default=15.0, help="seconds between LLM trials (rate limits)")
    ap.add_argument("--configs", nargs="*", default=FAULT_CONFIGS)
    ap.add_argument("--archs", nargs="*", default=LLM_ARCHITECTURES, choices=sorted(ARCHITECTURES))
    ap.add_argument("--tasks", nargs="*", default=[t.id for t in CATALOG], choices=sorted(BY_ID))
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--serve", action="store_true", help="run the toolserver and proxy in-process")
    ap.add_argument("--db", default=None, help=f"sqlite path (default {store.DB_PATH})")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    unknown = sorted(set(args.configs) - set(available()))
    if unknown:
        raise SystemExit(f"unknown fault configs {unknown}; have {available()}")

    backend = None
    if any(ARCHITECTURES[a].needs_llm for a in args.archs):
        from agents.llm import default_backend
        try:
            backend = default_backend()
        except LLMError as exc:
            raise SystemExit(f"{exc}. To run without a model, use --archs oracle")
    model_id = backend.model if backend else "none"

    stack = local_stack() if args.serve else nullcontext((settings.TOOLSERVER_URL, settings.PROXY_URL))
    with stack as (toolserver_url, proxy_url):
        preflight(toolserver_url, proxy_url)
        conn = store.connect(args.db)
        existing = store.find_sweep(conn, args.label)
        if existing and backend and existing["model_id"] not in (model_id, "none"):
            print(f"warning: sweep {args.label} was started with {existing['model_id']}, now running {model_id}")
        sweep_id = store.start_sweep(conn, args.label, model_id, args.repeats)
        done = store.done_cells(conn, sweep_id)

        # repeat-major order: if the run stops early, every cell has the same
        # number of repeats so far instead of whole configs missing
        cells = [
            (rep, cfg, BY_ID[tid], arch)
            for rep in range(args.repeats)
            for cfg in args.configs
            for tid in args.tasks
            for arch in args.archs
        ]
        todo = [c for c in cells if (c[2].id, c[3], c[1], c[0]) not in done]
        print(f"sweep {args.label}: {len(todo)} trials to run ({len(cells) - len(todo)} already done), model {model_id}")

        status, harness_streak = "done", 0
        try:
            for i, (rep, cfg, task, arch_id) in enumerate(todo, 1):
                arch = ARCHITECTURES[arch_id]
                seed = cell_seed(task.id, cfg, rep)
                set_fault_config(proxy_url, cfg, seed)
                result = run_trial(
                    task, arch_id, backend if arch.needs_llm else None, args.max_steps, toolserver_url, proxy_url
                )
                store.record(conn, sweep_id, cfg, rep, seed, result)

                flag = "F" if result.fault_fired else " "
                print(f"[{i}/{len(todo)}] {cfg:16} {task.id:18} {arch_id:7} r{rep} {flag} {result.outcome}")

                if result.outcome == "fail_harness":
                    harness_streak += 1
                    print(f"  harness: {result.reason[:200]}")
                    if "PerDay" in result.reason:
                        print("  daily quota exhausted, stopping. Rerun with the same --label to resume.")
                        status = "paused"
                        break
                    if harness_streak >= 3:
                        print("  three model API failures in a row, stopping. Rerun with the same --label to resume.")
                        status = "paused"
                        break
                else:
                    harness_streak = 0

                if arch.needs_llm and i < len(todo):
                    time.sleep(args.pace)
        except KeyboardInterrupt:
            print("\ninterrupted. Rerun with the same --label to resume.")
            status = "paused"

        store.finish_sweep(conn, sweep_id, status)
        print(f"{status}. Report: python -m sweep.report --label {args.label}")


if __name__ == "__main__":
    main()
