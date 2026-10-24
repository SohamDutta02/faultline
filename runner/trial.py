import argparse
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import httpx

import settings
from agents.llm import Backend, LLMError
from agents.loop import AgentContext, StepBudgetExceeded
from agents.registry import BadHarness, resolve
from tasks.catalog import BY_ID, CATALOG
from tasks.spec import Task
from tasks.verify import verify, world_diff


@dataclass
class TrialResult:
    task_id: str
    architecture_id: str
    outcome: str
    reason: str
    tool_calls: int
    tool_errors: int
    retries: int
    llm_calls: int
    tokens_used: int
    duration_ms: int
    fault_fired: bool
    answer: str | None
    events: list
    checks: list = field(default_factory=list)
    world_diff: dict = field(default_factory=dict)


def prepare_world(admin: httpx.Client, task: Task) -> dict:
    admin.post("/_admin/reset", params={"seed": task.seed}).raise_for_status()
    for order in task.setup:
        admin.post("/tools/place_order", params=order).raise_for_status()
    return admin.get("/_admin/state").json()


def run_trial(
    task: Task,
    architecture_id: str = "single",
    backend: Backend | None = None,
    max_steps: int = 12,
    toolserver_url: str | None = None,
    proxy_url: str | None = None,
    on_event: Callable | None = None,
) -> TrialResult:
    """Reset the world, let one architecture attempt one task through the proxy,
    and grade the world it leaves behind. The fault config is whatever the proxy
    currently has active. on_event receives each Event as it is logged."""
    arch = resolve(architecture_id)
    if arch.needs_llm and backend is None:
        raise ValueError(f"architecture {architecture_id} needs an LLM backend")

    with httpx.Client(base_url=toolserver_url or settings.TOOLSERVER_URL, timeout=15) as admin:
        before = prepare_world(admin, task)

        ctx = AgentContext(arch.make_toolbox(proxy_url or settings.PROXY_URL, on_event=on_event), backend, max_steps)
        answer, error = None, None
        start = time.perf_counter()
        try:
            answer = arch.run(task, ctx)
        except StepBudgetExceeded:
            error = "loop"
        except LLMError as exc:
            error = f"harness: {exc}"
        except Exception as exc:
            ctx.toolbox.log("crash", payload={"traceback": traceback.format_exc()[-2000:]})
            error = f"crash: {type(exc).__name__}: {exc}"
        finally:
            ctx.toolbox.close()
        duration_ms = int((time.perf_counter() - start) * 1000)

        after = admin.get("/_admin/state").json()

    tb = ctx.toolbox
    verdict = verify(task, before, after, answer, error)
    return TrialResult(
        task_id=task.id,
        architecture_id=architecture_id,
        outcome=verdict.outcome.value,
        reason=verdict.reason,
        tool_calls=tb.call_count,
        tool_errors=tb.error_count,
        retries=tb.retries,
        llm_calls=ctx.llm_calls,
        tokens_used=ctx.tokens,
        duration_ms=duration_ms,
        fault_fired=any(e.fault_injected for e in tb.events),
        answer=answer,
        events=[asdict(e) for e in tb.events],
        checks=[asdict(c) for c in verdict.checks],
        world_diff=world_diff(before, after),
    )


def arch_id(value: str) -> str:
    """argparse type for architecture ids: presets, scripted controls, or edited harnesses."""
    try:
        resolve(value)
    except BadHarness as exc:
        raise argparse.ArgumentTypeError(str(exc))
    return value


def main():
    ap = argparse.ArgumentParser(description="run tasks once against the proxy's current fault config")
    ap.add_argument("--arch", default="single", type=arch_id, help="a harness preset or edit, e.g. single+tools=retrying")
    ap.add_argument("--tasks", nargs="*", default=[t.id for t in CATALOG], choices=sorted(BY_ID))
    ap.add_argument("--pace", type=float, default=0.0, help="seconds to sleep between LLM trials")
    args = ap.parse_args()

    backend = None
    if resolve(args.arch).needs_llm:
        from agents.llm import default_backend
        backend = default_backend()

    for i, tid in enumerate(args.tasks):
        if i and backend is not None:
            time.sleep(args.pace)
        r = run_trial(BY_ID[tid], args.arch, backend)
        print(f"{r.task_id:20} {r.outcome:18} calls={r.tool_calls} tokens={r.tokens_used} {r.reason}")


if __name__ == "__main__":
    main()
