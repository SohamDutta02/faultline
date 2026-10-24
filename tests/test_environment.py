import json

import httpx
import pytest

from agents.llm import Reply, ScriptedBackend, ToolCall
from agents.registry import resolve
from agents.toolbox import ResilientToolbox, Toolbox
from runner.trial import run_trial
from tasks.catalog import BY_ID
from toolserver.state import Rejected, World


def test_a_request_id_makes_a_write_happen_once():
    w = World()
    w.reset(42)
    before = w.inventory["A-22"]["qty"]
    first = w.place_order("CUST-100", "A-22", 5, request_id="k1")
    again = w.place_order("CUST-100", "A-22", 5, request_id="k1")
    assert again == {**first, "replayed": True}
    assert len(w.orders) == 1 and w.inventory["A-22"]["qty"] == before - 5

    other = w.place_order("CUST-100", "A-22", 5)  # no key: a second order
    assert other["order_id"] == "ORD-0002"
    with pytest.raises(Rejected, match="different request"):
        w.place_order("CUST-100", "A-22", 4, request_id="k1")


def test_cancel_with_a_request_id_replays_instead_of_rejecting():
    w = World()
    w.reset(42)
    oid = w.place_order("CUST-101", "C-02", 4)["order_id"]
    assert w.cancel_order(oid, request_id="c1") == {"ok": True, "order_id": oid}
    assert w.cancel_order(oid, request_id="c1")["replayed"] is True
    with pytest.raises(Rejected, match="already cancelled"):
        w.cancel_order(oid)


def test_request_ids_pass_through_the_proxy(urls):
    toolserver_url, proxy_url = urls
    params = {"customer_id": "CUST-100", "sku": "A-22", "qty": 2, "request_id": "abc"}
    for _ in range(2):
        httpx.post(f"{proxy_url}/tools/place_order", params=params).raise_for_status()
    orders = httpx.get(f"{toolserver_url}/tools/list_orders", params={"customer_id": "CUST-100"}).json()["orders"]
    assert len(orders) == 1


def test_idempotent_toolbox_keys_writes_and_tells_the_model(urls):
    _, proxy_url = urls
    plain, keyed = Toolbox(proxy_url), Toolbox(proxy_url, idempotent=True)
    args = {"customer_id": "CUST-100", "sku": "A-22", "qty": 1}
    assert keyed.request_id("place_order", args) == keyed.request_id("place_order", dict(reversed(args.items())))
    assert keyed.request_id("place_order", args) != Toolbox(proxy_url, idempotent=True).request_id("place_order", args)

    desc = {t["name"]: t["description"] for t in keyed.llm_tools()}
    assert "Idempotent" in desc["place_order"] and "Idempotent" not in desc["search_inventory"]
    assert all("Idempotent" not in t["description"] for t in plain.llm_tools())

    keyed.call("place_order", args)
    keyed.call("place_order", args)
    result = [e for e in keyed.events if e.kind == "tool_result"][-1]
    assert json.loads(result.payload["body"])["replayed"] and result.payload["request_id"]
    plain.close()
    keyed.close()


def test_retrying_client_retries_idempotent_writes(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    tb = ResilientToolbox(proxy_url, idempotent=True)
    tb.base_backoff_s = 0
    body, is_error = tb.call("place_order", {"customer_id": "CUST-100", "sku": "A-22", "qty": 5})
    assert not is_error and json.loads(body)["replayed"] and tb.retries == 1
    orders = httpx.get(f"{toolserver_url}/tools/list_orders", params={"customer_id": "CUST-100"}).json()["orders"]
    assert len(orders) == 1
    tb.close()


def test_environment_slot_builds_the_toolbox():
    assert resolve("single+environment=idempotent").make_toolbox("http://x").idempotent
    assert not resolve("single").make_toolbox("http://x").idempotent
    assert resolve("oracle_retry+environment=idempotent").fingerprint != resolve("oracle_retry").fingerprint


def test_idempotent_environment_recovers_timed_out_writes_for_the_control(urls, faults):
    toolserver_url, proxy_url = urls
    outcomes = {}
    for arch in ("oracle_retry", "oracle_retry+environment=idempotent"):
        faults([{"type": "timeout", "target": "place_order", "max_fires": 1, "params": {"delay_s": 0}}])
        outcomes[arch] = run_trial(BY_ID["t2_simple_order"], arch, toolserver_url=toolserver_url,
                                   proxy_url=proxy_url).outcome
    assert outcomes == {"oracle_retry": "fail_wrong_answer", "oracle_retry+environment=idempotent": "pass"}


@pytest.mark.parametrize("arch, outcome", [("single", "fail_wrong_answer"),
                                           ("single+environment=idempotent", "pass")])
def test_a_model_repeating_an_ambiguous_write(urls, faults, arch, outcome):
    toolserver_url, proxy_url = urls
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    order = Reply(calls=[ToolCall("place_order", {"customer_id": "CUST-100", "sku": "A-22", "qty": 5})])
    backend = ScriptedBackend([order, order, Reply(text="Order placed.")])
    r = run_trial(BY_ID["t2_simple_order"], arch, backend, 12, toolserver_url, proxy_url)
    assert r.outcome == outcome, r.reason
