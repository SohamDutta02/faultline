import json

from agents.llm import Reply, ScriptedBackend, ToolCall
from harness.state import HEADER, Ledger
from runner.trial import run_trial
from tasks.catalog import BY_ID

ORDER = {"customer_id": "CUST-100", "sku": "A-22", "qty": 5}


def orders(*rows):
    return json.dumps({"orders": [
        {"order_id": oid, "customer_id": "CUST-100", "sku": sku, "qty": qty, "status": status}
        for oid, sku, qty, status in rows
    ]})


def test_an_empty_ledger_renders_nothing():
    ledger = Ledger()
    ledger.observe("search_inventory", {"sku": "A-22"}, '{"sku": "A-22", "qty": 12}', 200)
    assert ledger.render() == ""


def test_write_outcomes_follow_the_status():
    ledger = Ledger()
    ledger.observe("place_order", ORDER, '{"order_id": "ORD-0001"}', 200)
    ledger.observe("place_order", {**ORDER, "qty": 1}, '{"error": "rate_limited"}', 429)
    ledger.observe("place_order", {**ORDER, "qty": 2}, '{"error": "rejected", "detail": "credit limit exceeded"}', 409)
    ledger.observe("place_order", {**ORDER, "qty": 3}, '{"error": "internal"}', 500)
    ledger.observe("cancel_order", {"order_id": "ORD-0009"}, '{"orders":', 200)
    assert [w.outcome for w in ledger.writes] == ["done", "not_executed", "rejected", "unknown", "unknown"]
    text = ledger.render()
    assert text.startswith(HEADER)
    assert "place_order(customer_id=CUST-100, qty=5, sku=A-22): done (ORD-0001)" in text
    assert "safe to repeat" in text and "credit limit exceeded" in text
    assert "qty=3, sku=A-22): UNKNOWN. It returned 500 and may have gone through" in text


def test_list_orders_settles_unknown_writes():
    ledger = Ledger()
    ledger.observe("list_orders", {"customer_id": "CUST-100"}, orders(("ORD-0001", "A-22", 5, "placed")), 200)
    ledger.observe("place_order", ORDER, '{"error": "internal"}', 500)          # went through as ORD-0002
    ledger.observe("place_order", {**ORDER, "qty": 7}, '{"error": "gateway_timeout"}', 504)  # never ran
    ledger.observe("list_orders", {"customer_id": "CUST-100"}, orders(
        ("ORD-0001", "A-22", 5, "placed"), ("ORD-0002", "A-22", 5, "placed")), 200)
    went, lost = ledger.writes
    # ORD-0001 existed before the write, so it can't be what the write created
    assert (went.outcome, went.order_id) == ("went_through", "ORD-0002")
    assert lost.outcome == "did_not_go_through"
    assert "went through as ORD-0002" in ledger.render() and "Do not repeat it" in ledger.render()


def test_duplicates_are_called_out_but_replays_are_not():
    ledger = Ledger()
    ledger.observe("place_order", ORDER, '{"error": "internal"}', 500)
    ledger.observe("place_order", ORDER, '{"order_id": "ORD-0002"}', 200)
    ledger.observe("list_orders", {"customer_id": "CUST-100"}, orders(
        ("ORD-0001", "A-22", 5, "placed"), ("ORD-0002", "A-22", 5, "placed")), 200)
    assert "has gone through 2 times (ORD-0001, ORD-0002)" in ledger.render()

    keyed = Ledger()
    keyed.observe("place_order", ORDER, '{"order_id": "ORD-0001"}', 200)
    keyed.observe("place_order", ORDER, '{"order_id": "ORD-0001", "replayed": true}', 200)
    assert "has gone through" not in keyed.render() and "replayed" in keyed.render()


def test_contradictory_reads_are_flagged_unless_a_write_explains_them():
    ledger = Ledger()
    ledger.observe("get_pricing", {"sku": "D-40", "tier": "silver"}, '{"unit_price": 31.05}', 200)
    ledger.observe("search_inventory", {"sku": "A-22"}, '{"qty": 12, "updated_at": "t0"}', 200)
    ledger.observe("place_order", ORDER, '{"order_id": "ORD-0001"}', 200)
    ledger.observe("search_inventory", {"sku": "A-22"}, '{"qty": 7, "updated_at": "t1"}', 200)   # explained
    ledger.observe("get_pricing", {"sku": "D-40", "tier": "silver"}, '{"unit_price": 18.63}', 200)  # never changes
    text = ledger.render()
    assert "get_pricing(sku=D-40, tier=silver) gave different results" in text and "18.63" in text
    assert "search_inventory(sku=A-22) gave different" not in text


def test_unreadable_reads_are_flagged_until_a_good_read():
    ledger = Ledger()
    ledger.observe("get_customer", {"id": "CUST-100"}, '{"id": "CUST', 200)
    assert "unreadable" in ledger.render()
    ledger.observe("get_customer", {"id": "CUST-100"}, '{"id": "CUST-100"}', 200)
    assert ledger.render() == ""


def test_the_ledger_reaches_the_model(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])

    backend = ScriptedBackend([
        Reply(calls=[ToolCall("place_order", ORDER)]),
        Reply(calls=[ToolCall("list_orders", {"customer_id": "CUST-100"})]),
        Reply(text="The order went through as ORD-0001."),
    ])
    r = run_trial(BY_ID["t2_simple_order"], "single+state=ledger", backend, 12, toolserver_url, proxy_url)
    assert r.outcome == "pass", r.reason
    first, second, third = (req["system"] for req in backend.requests)
    assert HEADER not in first
    assert "UNKNOWN. It returned 500" in second
    assert "went through as ORD-0001" in third
    states = [e for e in r.events if e["kind"] == "state"]
    assert len(states) == 2 and states[-1]["payload"]["role"] == "agent"
