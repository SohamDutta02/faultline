import json

import httpx

from agents.critic import verdict
from agents.llm import Reply, ScriptedBackend, ToolCall
from agents.toolbox import ResilientToolbox
from runner.trial import run_trial
from tasks.catalog import BY_ID


def call(name, **args):
    return Reply(calls=[ToolCall(name, args)], tokens=10)


def say(text):
    return Reply(text=text, tokens=5)


def last_result(messages) -> dict:
    return json.loads(messages[-1]["results"][-1]["body"])


def trial(urls, task_id, arch, steps, max_steps=12):
    toolserver_url, proxy_url = urls
    backend = ScriptedBackend(steps)
    result = run_trial(BY_ID[task_id], arch, backend, max_steps, toolserver_url, proxy_url)
    return result, backend


def test_single_agent_completes_a_write(urls):
    r, _ = trial(urls, "t2_simple_order", "single", [
        call("place_order", customer_id="CUST-100", sku="A-22", qty=5),
        say("Order placed."),
    ])
    assert r.outcome == "pass", r.reason
    assert (r.tool_calls, r.llm_calls, r.tokens_used) == (1, 2, 15)
    assert [e["kind"] for e in r.events] == ["llm", "tool_call", "tool_result", "llm"]


def test_step_budget_is_a_loop_failure(urls):
    r, _ = trial(urls, "t1_stock_lookup", "single", [call("search_inventory", sku="A-22")] * 3, max_steps=3)
    assert r.outcome == "fail_loop"


def test_model_api_failure_is_a_harness_failure(urls):
    r, _ = trial(urls, "t1_stock_lookup", "single", [])
    assert r.outcome == "fail_harness"


def test_architecture_exception_is_a_crash(urls):
    def boom(messages):
        raise ValueError("bad parse")

    r, _ = trial(urls, "t1_stock_lookup", "single", [boom])
    assert r.outcome == "fail_crash"
    assert r.events[-1]["kind"] == "crash"


def test_retry_architecture_rides_out_rate_limits(urls, faults):
    faults([{"type": "rate_limit", "target": "search_inventory", "max_fires": 2, "params": {"retry_after": 0}}])
    r, _ = trial(urls, "t1_stock_lookup", "retry", [
        call("search_inventory", sku="A-22"),
        lambda m: say(str(last_result(m)["qty"])),
    ])
    assert r.outcome == "pass", r.reason
    assert (r.retries, r.tool_errors, r.fault_fired) == (2, 0, True)


def test_single_architecture_sees_the_raw_fault(urls, faults):
    faults([{"type": "rate_limit", "target": "search_inventory", "max_fires": 1, "params": {"retry_after": 0}}])
    r, backend = trial(urls, "t1_stock_lookup", "single", [
        call("search_inventory", sku="A-22"),
        call("search_inventory", sku="A-22"),
        lambda m: say(str(last_result(m)["qty"])),
    ])
    assert r.outcome == "pass"
    assert (r.retries, r.tool_errors) == (0, 1)
    first = backend.requests[1]["messages"][-1]["results"][0]
    assert first["is_error"] and "rate_limited" in first["body"]


def test_resilient_toolbox_policy(urls, faults):
    toolserver_url, proxy_url = urls
    tb = ResilientToolbox(proxy_url)
    tb.base_backoff_s = 0

    faults([{"type": "malformed_json", "target": "search_inventory", "max_fires": 1}])
    body, is_error = tb.call("search_inventory", {"sku": "A-22"})
    assert not is_error and json.loads(body)["sku"] == "A-22"

    # a 429'd write was never executed, so retrying it is safe
    faults([{"type": "rate_limit", "target": "place_order", "max_fires": 1, "params": {"retry_after": 0}}])
    _, is_error = tb.call("place_order", {"customer_id": "CUST-100", "sku": "A-22", "qty": 1})
    assert not is_error

    # a 5xx'd write may have committed, so it is handed to the agent as-is
    faults([{"type": "partial_write", "target": "place_order"}])
    _, is_error = tb.call("place_order", {"customer_id": "CUST-100", "sku": "A-22", "qty": 1})
    assert is_error
    tb.close()

    orders = httpx.get(f"{toolserver_url}/tools/list_orders", params={"customer_id": "CUST-100"}).json()["orders"]
    assert len(orders) == 2
    assert tb.retries == 2


def test_critic_catches_a_duplicate_from_partial_write(urls, faults):
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    r, backend = trial(urls, "t2_simple_order", "critic", [
        # actor: first write "fails" (but commits), so it retries and duplicates
        call("place_order", customer_id="CUST-100", sku="A-22", qty=5),
        call("place_order", customer_id="CUST-100", sku="A-22", qty=5),
        say("Order placed."),
        # critic
        call("list_orders", customer_id="CUST-100"),
        say("Two identical orders exist (ORD-0001, ORD-0002).\nVERDICT: REVISE"),
        # actor revision
        call("cancel_order", order_id="ORD-0002"),
        say("Cancelled the duplicate; one order remains."),
        # critic again
        say("VERDICT: OK"),
    ])
    assert r.outcome == "pass", r.reason
    critic_tools = backend.requests[3]["tools"]
    assert "place_order" not in critic_tools and "list_orders" in critic_tools
    assert [e["payload"]["verdict"] for e in r.events if e["kind"] == "critic"] == ["revise", "ok"]
    review_prompt = backend.requests[3]["messages"][0]["text"]
    assert "place_order" in review_prompt and "[ERROR]" in review_prompt


def test_critic_cannot_write(urls):
    r, _ = trial(urls, "t1_stock_lookup", "critic", [
        call("search_inventory", sku="A-22"),
        lambda m: say(str(last_result(m)["qty"])),
        call("place_order", customer_id="CUST-100", sku="A-22", qty=1),
        say("VERDICT: OK"),
    ])
    assert r.outcome == "pass", r.reason
    rejected = [e for e in r.events if e["kind"] == "tool_call" and e["tool_name"] == "place_order"]
    assert rejected == []


def test_verdict_parsing():
    assert verdict("looks fine\nVERDICT: OK") == "ok"
    assert verdict("verdict: revise - wrong qty") == "revise"
    assert verdict("VERDICT: REVISE\n...\nVERDICT: OK") == "ok"
    assert verdict(None) == "ok"
    assert verdict("no verdict line") == "ok"


def test_oracle_gives_up_cleanly_on_faults(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "rate_limit", "target": "*", "params": {"retry_after": 0}}])
    r = run_trial(BY_ID["t3_budget_order"], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
    assert r.outcome == "fail_wrong_answer"
    assert r.answer.startswith("gave up: get_customer failed")


def test_events_stream_to_a_listener(urls):
    toolserver_url, proxy_url = urls
    seen = []
    r = run_trial(BY_ID["t2_simple_order"], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url,
                  on_event=seen.append)
    assert [e.kind for e in seen] == [e["kind"] for e in r.events] == ["tool_call", "tool_result"]


def test_injected_fault_is_named_in_the_event(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "stale_data", "target": "search_inventory"}])
    r = run_trial(BY_ID["t1_stock_lookup"], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
    result = [e for e in r.events if e["kind"] == "tool_result"][0]
    assert result["fault_injected"] and result["payload"]["fault"] == "stale_data"


def test_retrying_client_fixes_transport_faults_for_the_oracle(urls, faults):
    toolserver_url, proxy_url = urls
    outcomes = {}
    for arch in ("oracle", "oracle_retry"):
        faults([{"type": "rate_limit", "max_fires": 1, "params": {"retry_after": 0}}])
        outcomes[arch] = run_trial(BY_ID["t5_tier_quote"], arch, toolserver_url=toolserver_url,
                                   proxy_url=proxy_url).outcome
    assert outcomes == {"oracle": "fail_wrong_answer", "oracle_retry": "pass"}


def test_model_turns_are_tagged_by_role(urls):
    r, _ = trial(urls, "t1_stock_lookup", "critic", [say("11"), say("VERDICT: OK")])
    assert [e["payload"]["role"] for e in r.events if e["kind"] == "llm"] == ["agent", "reviewer"]
