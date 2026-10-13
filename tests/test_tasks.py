import httpx
import pytest

from runner.trial import prepare_world, run_trial
from tasks.catalog import BY_ID, CATALOG
from tasks.spec import Outcome
from tasks.verify import as_number, verify


@pytest.mark.parametrize("task", CATALOG, ids=lambda t: t.id)
def test_oracle_passes_every_task_without_faults(urls, task):
    toolserver_url, proxy_url = urls
    r = run_trial(task, "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
    assert r.outcome == "pass", r.reason
    assert not r.fault_fired


def grade(urls, task_id, actions, answer="done"):
    """Prepare the task's world, apply `actions` directly on the toolserver, grade."""
    toolserver_url, _ = urls
    task = BY_ID[task_id]
    with httpx.Client(base_url=toolserver_url) as c:
        before = prepare_world(c, task)
        for path, params in actions:
            c.post(path, params=params)
        after = c.get("/_admin/state").json()
    return verify(task, before, after, answer)


def order(customer_id, sku, qty):
    return ("/tools/place_order", {"customer_id": customer_id, "sku": sku, "qty": qty})


def test_t1_wrong_number_and_mutation(urls):
    assert grade(urls, "t1_stock_lookup", [], answer="11").passed
    assert grade(urls, "t1_stock_lookup", [], answer="12").outcome is Outcome.FAIL_WRONG_ANSWER
    mutated = grade(urls, "t1_stock_lookup", [order("CUST-100", "A-11", 1)], answer="11")
    assert "world untouched" in mutated.reason


def test_t2_duplicate_order_fails(urls):
    assert grade(urls, "t2_simple_order", [order("CUST-100", "A-22", 5)]).passed
    v = grade(urls, "t2_simple_order", [order("CUST-100", "A-22", 5)] * 2)
    assert "expected 1 matching order, found 2" in v.reason


def test_t3_is_credit_bound(urls):
    toolserver_url, _ = urls
    with httpx.Client(base_url=toolserver_url) as c:
        before = prepare_world(c, BY_ID["t3_budget_order"])
    stock = before["inventory"]["D-40"]["qty"]
    assert 22 < stock, "credit limit, not stock, must be the binding constraint"
    assert grade(urls, "t3_budget_order", [order("CUST-102", "D-40", 22)]).passed
    assert not grade(urls, "t3_budget_order", [order("CUST-102", "D-40", 21)]).passed


def test_t4_must_leave_other_orders(urls):
    # setup creates ORD-0001 (A-31), ORD-0002 (the mistaken C-02), ORD-0003 (another customer's C-02)
    cancel = lambda oid: ("/tools/cancel_order", {"order_id": oid})
    assert grade(urls, "t4_cancel_order", [cancel("ORD-0002")]).passed
    v = grade(urls, "t4_cancel_order", [cancel("ORD-0002"), cancel("ORD-0003")])
    assert "other orders untouched" in v.reason
    assert "mistaken order cancelled" in grade(urls, "t4_cancel_order", []).reason


def test_t5_quote(urls):
    assert grade(urls, "t5_tier_quote", [], answer="$310.50").passed
    assert not grade(urls, "t5_tier_quote", [], answer="337.5").passed


def test_t6_orders_what_is_available(urls):
    assert grade(urls, "t6_stock_fallback", [order("CUST-103", "C-09", 3)]).passed
    assert not grade(urls, "t6_stock_fallback", [order("CUST-103", "C-09", 2)]).passed


def test_error_outcomes_short_circuit_checks():
    task = BY_ID["t1_stock_lookup"]
    assert verify(task, {}, {}, None, "loop").outcome is Outcome.FAIL_LOOP
    assert verify(task, {}, {}, None, "harness: quota").outcome is Outcome.FAIL_HARNESS
    assert verify(task, {}, {}, None, "crash: KeyError").outcome is Outcome.FAIL_CRASH
    assert verify(task, {}, {}, None).outcome is Outcome.FAIL_NO_ANSWER


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("11", 11),
        ("A-22 has 11 units", 11),
        ("There are 11 units of A-22.", 11),
        ("$1,234.50", 1234.5),
        ("CUST-101 would pay 310.5", 310.5),
        ("gave up: A-22 failed", None),
        (7, 7),
    ],
)
def test_as_number_ignores_identifiers(answer, expected):
    assert as_number(answer) == expected


def test_trial_reports_checks_and_world_diff(urls):
    toolserver_url, proxy_url = urls
    r = run_trial(BY_ID["t4_cancel_order"], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
    assert [c["passed"] for c in r.checks] == [True, True, True]
    diff = r.world_diff
    assert diff["orders_added"] == []
    assert [(c["order_id"], c["before"], c["after"]) for c in diff["orders_changed"]] == [
        ("ORD-0002", "placed", "cancelled")
    ]
    assert [(i["sku"], i["after"] - i["before"]) for i in diff["inventory"]] == [("C-02", 4)]
