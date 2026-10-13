"""Scripted per-task policies that know the right sequence of calls.

The oracle is the control column of a sweep: it shows how a correct but naive
client (no retries, no cross-checks) fares under each fault, and it lets the
whole pipeline run without a model API key. It never retries; on a tool
failure it gives up and reports why, and the verifier judges the world state
it left behind.
"""

import json

from tasks.spec import Task

from .loop import AgentContext
from .toolbox import Toolbox


class ToolFailed(Exception):
    pass


def _call(tb: Toolbox, name: str, **args) -> dict:
    body, is_error = tb.call(name, args)
    if is_error:
        raise ToolFailed(f"{name} failed: {body[:200]}")
    try:
        return json.loads(body)
    except ValueError:
        raise ToolFailed(f"{name} returned unparseable body: {body[:200]}")


def _budget_order(tb: Toolbox, customer_id: str, sku: str) -> str:
    cust = _call(tb, "get_customer", id=customer_id)
    unit = _call(tb, "get_pricing", sku=sku, tier=cust["tier"])["unit_price"]
    stock = _call(tb, "search_inventory", sku=sku)["qty"]
    orders = _call(tb, "list_orders", customer_id=customer_id)["orders"]
    committed = sum(o["total"] for o in orders if o["status"] == "placed")
    qty = min(int((cust["credit_limit"] - committed) // unit), stock)
    _call(tb, "place_order", customer_id=customer_id, sku=sku, qty=qty)
    return f"ordered {qty}"


def _t1(tb):
    return str(_call(tb, "search_inventory", sku="A-22")["qty"])


def _t2(tb):
    _call(tb, "place_order", customer_id="CUST-100", sku="A-22", qty=5)
    return "done"


def _t3(tb):
    return _budget_order(tb, "CUST-102", "D-40")


def _t4(tb):
    orders = _call(tb, "list_orders", customer_id="CUST-101")["orders"]
    target = [o for o in orders if o["sku"] == "C-02" and o["status"] == "placed"]
    for o in target:
        _call(tb, "cancel_order", order_id=o["order_id"])
    return f"cancelled {len(target)}"


def _t5(tb):
    cust = _call(tb, "get_customer", id="CUST-101")
    unit = _call(tb, "get_pricing", sku="D-40", tier=cust["tier"])["unit_price"]
    return f"{round(unit * 10, 2)}"


def _t6(tb):
    stock = _call(tb, "search_inventory", sku="C-09")["qty"]
    qty = min(20, stock)
    _call(tb, "place_order", customer_id="CUST-103", sku="C-09", qty=qty)
    return f"ordered {qty}"


SOLVERS = {
    "t1_stock_lookup": _t1,
    "t2_simple_order": _t2,
    "t3_budget_order": _t3,
    "t4_cancel_order": _t4,
    "t5_tier_quote": _t5,
    "t6_stock_fallback": _t6,
}


def run(task: Task, ctx: AgentContext) -> str | None:
    try:
        return SOLVERS[task.id](ctx.toolbox)
    except ToolFailed as exc:
        return f"gave up: {exc}"
