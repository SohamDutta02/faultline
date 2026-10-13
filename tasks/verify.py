import re
from typing import Any

from toolserver.fixtures import TIER_MULTIPLIER

from .spec import CheckResult, Outcome, Task, Verdict


def verify(task: Task, before: dict, after: dict, answer: Any, error: str | None = None) -> Verdict:
    if error == "loop":
        return Verdict(Outcome.FAIL_LOOP, [], "step budget exhausted")
    if error and error.startswith("harness"):
        return Verdict(Outcome.FAIL_HARNESS, [], error)
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


def unit_price(state: dict, sku: str, customer_id: str) -> float:
    tier = state["customers"][customer_id]["tier"]
    return round(state["inventory"][sku]["base_price"] * TIER_MULTIPLIER[tier], 2)


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
        # skip digits that belong to identifiers like A-22 or CUST-102
        m = re.search(r"(?<![\w-])-?\d+(?:\.\d+)?(?![\w-])", answer.replace(",", ""))
        if m:
            return float(m.group())
    return None


def world_diff(before: dict, after: dict) -> dict:
    """What the trial changed: stock levels, new orders, order status changes."""
    inventory = [
        {"sku": sku, "name": row["name"], "before": row["qty"], "after": after["inventory"][sku]["qty"]}
        for sku, row in before["inventory"].items()
        if row["qty"] != after["inventory"][sku]["qty"]
    ]
    added = [o for oid, o in after["orders"].items() if oid not in before["orders"]]
    changed = [
        {**{k: o[k] for k in ("order_id", "customer_id", "sku", "qty")},
         "before": o["status"], "after": after["orders"][oid]["status"]}
        for oid, o in before["orders"].items()
        if oid in after["orders"] and after["orders"][oid]["status"] != o["status"]
    ]
    return {"inventory": inventory, "orders_added": added, "orders_changed": changed}
