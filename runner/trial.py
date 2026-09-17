import time
import os
from dataclasses import asdict, dataclass

import httpx

from agents import single
from agents.toolbox import Toolbox
from tasks.catalog import BY_ID
from tasks.spec import Task
from tasks.verify import verify

BASE = os.getenv("TOOLSERVER_URL", "http://127.0.0.1:8100")


@dataclass
class TrialResult:
    task_id: str
    architecture_id: str
    outcome: str
    reason: str
    tool_calls: int
    retries: int
    tokens_used: int
    fault_fired: bool
    answer: str | None
    events: list


def snapshot(c) -> dict:
    return c.get(f"{BASE}/_admin/state").json()


def run_trial(task: Task, architecture_id: str = "single", max_steps: int = 12) -> TrialResult:
    with httpx.Client(timeout=15) as c:
        c.post(f"{BASE}/_admin/reset", params={"seed": task.seed})
        before = snapshot(c)

        toolbox = Toolbox()
        try:
            res = single.run(task.prompt, toolbox, max_steps=max_steps)
        finally:
            toolbox.close()

        after = snapshot(c)

    verdict = verify(task, before, after, res.answer, res.error)
    return TrialResult(
        task_id=task.id,
        architecture_id=architecture_id,
        outcome=verdict.outcome.value,
        reason=verdict.reason,
        tool_calls=res.tool_calls,
        retries=res.retries,
        tokens_used=res.tokens_used,
        fault_fired=any(e.fault_injected for e in res.events),
        answer=res.answer,
        events=[asdict(e) for e in res.events],
    )


if __name__ == "__main__":
    for tid in ["t1_stock_lookup", "t2_simple_order", "t3_budget_order"]:
        r = run_trial(BY_ID[tid])
        print(f"{r.task_id:20} {r.outcome:20} calls={r.tool_calls} tokens={r.tokens_used} {r.reason}")
        time.sleep(20)