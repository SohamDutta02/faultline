import argparse
import time
import zlib
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass

import httpx

import settings
from agents.llm import LLMError
from agents.registry import BadHarness, ablation, resolve
from proxy.config import available
from runner.stack import local_stack
from runner.trial import arch_id, run_trial
from tasks.catalog import BY_ID, CATALOG

from . import store

FAULT_CONFIGS = ["none", "rate_limit", "timeout", "malformed_json", "stale_data", "plausible_wrong", "partial_write"]
# the default sweep's architectures; --ablate builds a controlled-variable set instead
LLM_ARCHITECTURES = ["single", "retry", "critic"]


def ablate_arg(value: str) -> str:
    try:
        ablation(value)
    except BadHarness as exc:
        raise argparse.ArgumentTypeError(str(exc))
    return value


def choose_archs(archs: list[str] | None, ablate: str | None) -> list[str]:
    """--archs, plus the ablation set around --ablate; the default set if neither."""
    if ablate is None:
        return archs or LLM_ARCHITECTURES
    return list(dict.fromkeys((archs or []) + ablation(ablate)))


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


@dataclass
class Cell:
    repeat_idx: int
    fault_config: str
    task_id: str
    architecture_id: str


def plan(repeats: int, configs: list[str], tasks: list[str], archs: list[str], done: set) -> tuple[int, list[Cell]]:
    """All cells of a sweep and the ones still to run.

    Repeat-major order: if the run stops early, every cell has the same number
    of repeats so far instead of whole configs missing."""
    cells = [
        Cell(rep, cfg, tid, arch)
        for rep in range(repeats)
        for cfg in configs
        for tid in tasks
        for arch in archs
    ]
    todo = [c for c in cells if (c.task_id, c.architecture_id, c.fault_config, c.repeat_idx) not in done]
    return len(cells), todo


def execute(
    conn,
    sweep_id: str,
    todo: list[Cell],
    backend,
    toolserver_url: str,
    proxy_url: str,
    max_steps: int = 12,
    pace: float = 15.0,
    on_progress: Callable | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    """Run cells in order, recording each trial. Returns the sweep's new status:
    done, paused (model API trouble; resumable), or stopped (should_stop)."""
    stop = should_stop or (lambda: False)
    harness_streak = 0
    for i, cell in enumerate(todo, 1):
        if stop():
            return "stopped"
        arch = resolve(cell.architecture_id)
        seed = cell_seed(cell.task_id, cell.fault_config, cell.repeat_idx)
        set_fault_config(proxy_url, cell.fault_config, seed)
        result = run_trial(
            BY_ID[cell.task_id], cell.architecture_id, backend if arch.needs_llm else None,
            max_steps, toolserver_url, proxy_url,
        )
        trial_id = store.record(conn, sweep_id, cell.fault_config, cell.repeat_idx, seed, result)

        note = None
        if result.outcome == "fail_harness":
            harness_streak += 1
            if "PerDay" in result.reason:
                note = "daily quota exhausted"
            elif harness_streak >= 3:
                note = "three model API failures in a row"
        else:
            harness_streak = 0
        if on_progress:
            on_progress(i, len(todo), cell, result, trial_id, note)
        if note:
            return "paused"

        if arch.needs_llm and i < len(todo):
            deadline = time.monotonic() + pace
            while time.monotonic() < deadline:
                if stop():
                    return "stopped"
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    return "done"


def build_parser():
    ap = argparse.ArgumentParser(description="run every (task, architecture, fault config, repeat) cell")
    ap.add_argument("--label", required=True, help="sweep name; rerun with the same label to resume")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--pace", type=float, default=15.0, help="seconds between LLM trials (rate limits)")
    ap.add_argument("--configs", nargs="*", default=FAULT_CONFIGS)
    ap.add_argument("--archs", nargs="*", default=None, type=arch_id,
                    help="harness presets or edits (single+feedback=critic), or the scripted oracle controls "
                         f"(default: {' '.join(LLM_ARCHITECTURES)})")
    ap.add_argument("--ablate", metavar="HARNESS", type=ablate_arg,
                    help="add a controlled-variable set around HARNESS: the baseline, the baseline plus each "
                         "subsystem HARNESS changes, HARNESS minus each, and HARNESS (e.g. --ablate full)")
    ap.add_argument("--tasks", nargs="*", default=[t.id for t in CATALOG], choices=sorted(BY_ID))
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--serve", action="store_true", help="run the toolserver and proxy in-process")
    ap.add_argument("--db", default=None, help=f"sqlite path (default {store.DB_PATH})")
    return ap


def _print_progress(i, n, cell, result, trial_id, note):
    flag = "F" if result.fault_fired else " "
    print(f"[{i}/{n}] {cell.fault_config:16} {cell.task_id:18} {cell.architecture_id:12} "
          f"r{cell.repeat_idx} {flag} {result.outcome}")
    if result.outcome == "fail_harness":
        print(f"  harness: {result.reason[:200]}")
    if note:
        print(f"  {note}, stopping.")


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.archs = choose_archs(args.archs, args.ablate)

    unknown = sorted(set(args.configs) - set(available()))
    if unknown:
        raise SystemExit(f"unknown fault configs {unknown}; have {available()}")

    backend = None
    if any(resolve(a).needs_llm for a in args.archs):
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
        params = {"archs": args.archs, "configs": args.configs, "tasks": args.tasks, "repeats": args.repeats}
        sweep_id = store.start_sweep(conn, args.label, model_id, args.repeats, params)
        total, todo = plan(args.repeats, args.configs, args.tasks, args.archs, store.done_cells(conn, sweep_id))
        print(f"sweep {args.label}: {len(todo)} trials to run ({total - len(todo)} already done), model {model_id}")

        try:
            status = execute(
                conn, sweep_id, todo, backend, toolserver_url, proxy_url,
                args.max_steps, args.pace, on_progress=_print_progress,
            )
        except KeyboardInterrupt:
            print("\ninterrupted.")
            status = "paused"

        store.finish_sweep(conn, sweep_id, status)
        if status != "done":
            print(f"Rerun with --label {args.label} to resume.")
        print(f"{status}. Report: python -m sweep.report --label {args.label}")


if __name__ == "__main__":
    main()
