from .spec import Task, check
from .verify import active_orders, as_number, qty_of, world_unchanged


@check("answer matches stock")
def _t1_answer(before, after, answer):
    expected = qty_of(before, "A-22")
    got = as_number(answer)
    if got is None:
        return False, f"no number in answer: {answer!r}"
    return int(got) == expected, f"expected {expected}, got {int(got)}"


@check("world untouched")
def _t1_readonly(before, after, answer):
    return world_unchanged(before, after)


@check("order placed correctly")
def _t2_order(before, after, answer):
    orders = active_orders(after, "CUST-100")
    matching = [o for o in orders if o["sku"] == "A-22" and o["qty"] == 5]
    if len(matching) != 1:
        return False, f"expected 1 matching order, found {len(matching)} of {len(orders)}"
    return True, ""


@check("inventory decremented")
def _t2_inventory(before, after, answer):
    delta = qty_of(before, "A-22") - qty_of(after, "A-22")
    return delta == 5, f"expected delta 5, got {delta}"


@check("affordable quantity ordered")
def _t3_qty(before, after, answer):
    unit = round(before["inventory"]["B-17"]["base_price"] * 1.0, 2)
    limit = 750.0
    expected = min(int(limit // unit), qty_of(before, "B-17"))
    orders = [o for o in active_orders(after, "CUST-102") if o["sku"] == "B-17"]
    if len(orders) != 1:
        return False, f"expected 1 B-17 order, found {len(orders)}"
    got = orders[0]["qty"]
    return got == expected, f"expected {expected}, got {got}"


@check("within credit limit")
def _t3_credit(before, after, answer):
    spent = sum(o["total"] for o in active_orders(after, "CUST-102"))
    return spent <= 750.0, f"spent {spent} against limit 750.0"


CATALOG = [
    Task(
        id="t1_stock_lookup",
        prompt="How many units of SKU A-22 are currently in stock? Reply with just the number.",
        category="read_only",
        seed=42,
        min_tool_calls=1,
        checks=[_t1_answer, _t1_readonly],
    ),
    Task(
        id="t2_simple_order",
        prompt="Place an order for 5 units of SKU A-22 for customer CUST-100.",
        category="single_write",
        seed=42,
        min_tool_calls=2,
        checks=[_t2_order, _t2_inventory],
    ),
    Task(
        id="t3_budget_order",
        prompt=(
            "Customer CUST-102 wants as many units of SKU B-17 as they can afford "
            "within their credit limit. Place that order."
        ),
        category="read_then_write",
        seed=42,
        min_tool_calls=3,
        checks=[_t3_qty, _t3_credit],
    ),
]

BY_ID = {t.id: t for t in CATALOG}