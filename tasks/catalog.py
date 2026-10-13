from .spec import Task, check
from .verify import active_orders, as_number, qty_of, unit_price, world_unchanged


@check("answer matches stock")
def _t1_answer(before, after, answer):
    expected = qty_of(before, "A-22")
    got = as_number(answer)
    if got is None:
        return False, f"no number in answer: {answer!r}"
    return int(got) == expected, f"expected {expected}, got {int(got)}"


@check("world untouched")
def _readonly(before, after, answer):
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


def _t3_expected(before):
    cust = before["customers"]["CUST-102"]
    committed = sum(o["total"] for o in active_orders(before, "CUST-102"))
    unit = unit_price(before, "D-40", "CUST-102")
    return min(int((cust["credit_limit"] - committed) // unit), qty_of(before, "D-40"))


@check("affordable quantity ordered")
def _t3_qty(before, after, answer):
    expected = _t3_expected(before)
    orders = [o for o in active_orders(after, "CUST-102") if o["sku"] == "D-40"]
    if len(orders) != 1:
        return False, f"expected 1 D-40 order, found {len(orders)}"
    got = orders[0]["qty"]
    return got == expected, f"expected {expected}, got {got}"


@check("within credit limit")
def _t3_credit(before, after, answer):
    limit = before["customers"]["CUST-102"]["credit_limit"]
    spent = sum(o["total"] for o in active_orders(after, "CUST-102"))
    return spent <= limit, f"spent {spent} against limit {limit}"


def _mistaken(before):
    return [o for o in active_orders(before, "CUST-101") if o["sku"] == "C-02"]


@check("mistaken order cancelled")
def _t4_cancelled(before, after, answer):
    targets = _mistaken(before)
    still_open = [o["order_id"] for o in targets if after["orders"][o["order_id"]]["status"] != "cancelled"]
    return not still_open, f"still open: {still_open}"


@check("other orders untouched")
def _t4_others(before, after, answer):
    target_ids = {o["order_id"] for o in _mistaken(before)}
    if set(after["orders"]) != set(before["orders"]):
        return False, f"order set changed: {sorted(set(after['orders']) - set(before['orders']))} added"
    changed = [
        oid for oid, o in before["orders"].items()
        if oid not in target_ids and after["orders"][oid]["status"] != o["status"]
    ]
    return not changed, f"changed: {changed}"


@check("stock restored")
def _t4_stock(before, after, answer):
    restored = sum(o["qty"] for o in _mistaken(before))
    expected = qty_of(before, "C-02") + restored
    got = qty_of(after, "C-02")
    return got == expected, f"expected C-02 stock {expected}, got {got}"


@check("quote matches tier price")
def _t5_quote(before, after, answer):
    expected = round(unit_price(before, "D-40", "CUST-101") * 10, 2)
    got = as_number(answer)
    if got is None:
        return False, f"no number in answer: {answer!r}"
    return abs(got - expected) < 0.01, f"expected {expected}, got {got}"


def _t6_expected(before):
    return min(20, qty_of(before, "C-09"))


@check("available quantity ordered")
def _t6_order(before, after, answer):
    expected = _t6_expected(before)
    orders = [o for o in active_orders(after, "CUST-103") if o["sku"] == "C-09"]
    if len(orders) != 1:
        return False, f"expected 1 C-09 order, found {len(orders)}"
    got = orders[0]["qty"]
    return got == expected, f"expected {expected}, got {got}"


@check("inventory matches order")
def _t6_inventory(before, after, answer):
    delta = qty_of(before, "C-09") - qty_of(after, "C-09")
    expected = _t6_expected(before)
    return delta == expected, f"expected delta {expected}, got {delta}"


CATALOG = [
    Task(
        id="t1_stock_lookup",
        prompt="How many units of SKU A-22 are currently in stock? Reply with just the number.",
        category="read_only",
        seed=42,
        min_tool_calls=1,
        checks=[_t1_answer, _readonly],
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
            "Customer CUST-102 wants as many units of SKU D-40 as they can afford "
            "within their credit limit. Place that order."
        ),
        category="read_then_write",
        seed=42,
        min_tool_calls=3,
        checks=[_t3_qty, _t3_credit],
    ),
    Task(
        id="t4_cancel_order",
        prompt=(
            "Customer CUST-101 placed an order for SKU C-02 by mistake. "
            "Cancel that order and leave their other orders alone."
        ),
        category="corrective_write",
        seed=42,
        min_tool_calls=2,
        checks=[_t4_cancelled, _t4_others, _t4_stock],
        setup=[
            {"customer_id": "CUST-101", "sku": "A-31", "qty": 50},
            {"customer_id": "CUST-101", "sku": "C-02", "qty": 4},
            {"customer_id": "CUST-100", "sku": "C-02", "qty": 2},
        ],
    ),
    Task(
        id="t5_tier_quote",
        prompt=(
            "What would customer CUST-101 pay in total for 10 units of SKU D-40 at their "
            "pricing tier? Reply with just the number."
        ),
        category="read_only",
        seed=42,
        min_tool_calls=2,
        checks=[_t5_quote, _readonly],
    ),
    Task(
        id="t6_stock_fallback",
        prompt=(
            "Customer CUST-103 needs 20 units of SKU C-09. If there isn't enough stock, "
            "order however many are available instead."
        ),
        category="read_then_write",
        seed=42,
        min_tool_calls=2,
        checks=[_t6_order, _t6_inventory],
    ),
]

BY_ID = {t.id: t for t in CATALOG}
