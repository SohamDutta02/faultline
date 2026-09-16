from typing import Any

from .spec import CheckResult, Outcome, Task, Verdict


def verify(task: Task, before: dict, after: dict, answer: Any, error: str | None = None) -> Verdict:
    if error == "loop":
        return Verdict(Outcome.FAIL_LOOP, [], "step budget exhausted")
    if error == "harness":
        return Verdict(Outcome.FAIL_HARNESS, [], "harness failure")
    if error:
        return Verdict(Outcome.FAIL_CRASH, [], error)
    if answer is None:
        return Verdict(Outcome.FAIL_NO_ANSWER, [], "agent produced no answer")

    results = [c(before, after, answer) for c in task.checks]
    failed = [r for r in results if not r.passed]
    if failed:
        return Verdict(
            Outcome.FAIL_WRONG_ANSWER,
            results,
            "; ".join(f"{r.name}: {r.detail}" for r in failed),
        )
    return Verdict(Outcome.PASS, results, "")


def qty_of(state: dict, sku: str) -> int:
    return state["inventory"][sku]["qty"]


def active_orders(state: dict, customer_id: str | None = None) -> list[dict]:
    out = [o for o in state["orders"].values() if o["status"] == "placed"]
    if customer_id:
        out = [o for o in out if o["customer_id"] == customer_id]
    return out


def world_unchanged(before: dict, after: dict) -> tuple[bool, str]:
    if before["inventory"] != after["inventory"]:
        return False, "inventory mutated"
    if before["orders"] != after["orders"]:
        return False, "orders mutated"
    return True, ""


def as_number(answer: Any) -> float | None:
    if isinstance(answer, (int, float)):
        return float(answer)
    if isinstance(answer, str):
        import re
        m = re.search(r"-?\d+(?:\.\d+)?", answer.replace(",", ""))
        if m:
            return float(m.group())
    return None