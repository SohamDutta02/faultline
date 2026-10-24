import json
import re

import pytest

from agents.llm import Reply, ScriptedBackend, ToolCall
from agents.toolbox import Toolbox
from harness.checks import agent_calls, reader, run_checks
from runner.trial import TrialResult, run_trial
from sweep import report, store
from tasks.catalog import BY_ID, CATALOG

ORDER = {"customer_id": "CUST-100", "sku": "A-22", "qty": 5}


def call(name, **args):
    return Reply(calls=[ToolCall(name, args)], tokens=10)


def say(text):
    return Reply(text=text, tokens=5)


def check_oracle(urls, task_id, via="truth"):
    """Run the oracle, then the checks over its trace, reading via the toolserver or the proxy."""
    toolserver_url, proxy_url = urls
    r = run_trial(BY_ID[task_id], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
    tb = Toolbox(toolserver_url if via == "truth" else proxy_url)
    problems = run_checks(agent_calls(r.events), reader(tb), r.answer)
    tb.close()
    return r, problems


# ---- calibrating the verifier: get the verification commands right first ---------

@pytest.mark.parametrize("task_id", [t.id for t in CATALOG])
@pytest.mark.parametrize("via", ["truth", "proxy"])
def test_checks_raise_no_false_alarms_on_correct_work(urls, task_id, via):
    r, problems = check_oracle(urls, task_id, via)
    assert r.outcome == "pass"
    assert problems == []


def test_checks_see_a_write_that_failed_but_went_through(urls, faults):
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    r, problems = check_oracle(urls, "t2_simple_order")
    assert r.answer.startswith("gave up")
    assert len(problems) == 1 and "went through as ORD-0001" in problems[0].text


def test_checks_see_a_write_that_never_happened(urls, faults):
    faults([{"type": "timeout", "target": "place_order", "max_fires": 1, "params": {"delay_s": 0}}])
    _, problems = check_oracle(urls, "t2_simple_order")
    assert len(problems) == 1 and "did not go through" in problems[0].text


def test_only_an_independent_channel_catches_stale_reads(urls, faults):
    faults([{"type": "stale_data", "target": "search_inventory"}])
    r, through_truth = check_oracle(urls, "t1_stock_lookup", via="truth")
    assert r.outcome == "fail_wrong_answer"
    assert len(through_truth) == 1 and "search_inventory(sku=A-22): you saw qty" in through_truth[0].text
    assert through_truth[0].kind == "fact"

    faults([{"type": "stale_data", "target": "search_inventory"}])
    _, through_proxy = check_oracle(urls, "t1_stock_lookup", via="proxy")
    assert through_proxy == []  # the re-check is fooled the same way


def test_checks_allow_for_the_agents_own_orders(urls):
    # t3 reads stock, then orders: a re-check must expect the lower stock
    _, problems = check_oracle(urls, "t3_budget_order")
    assert problems == []


# ---- feedback in a trial ------------------------------------------------------

def trial(urls, arch, task_id, steps):
    toolserver_url, proxy_url = urls
    backend = ScriptedBackend(steps)
    return run_trial(BY_ID[task_id], arch, backend, 12, toolserver_url, proxy_url), backend


def test_checks_feedback_fixes_a_duplicate(urls, faults):
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    r, backend = trial(urls, "single+feedback=checks", "t2_simple_order", [
        call("place_order", **ORDER),
        call("place_order", **ORDER),          # the first one "failed", so it tries again
        say("Order placed."),
        call("cancel_order", order_id="ORD-0002"),
        say("Cancelled the duplicate."),
    ])
    assert r.outcome == "pass", r.reason
    assert r.feedback["option"] == "checks"
    assert r.feedback["first_pass"]["outcome"] == "fail_wrong_answer"
    assert [x["verdict"] for x in r.feedback["reviews"]] == ["revise", "ok"]
    revise_prompt = backend.requests[3]["messages"][-1]["text"]
    assert "2 matching orders exist (ORD-0001, ORD-0002)" in revise_prompt

    fb = [e for e in r.events if e["kind"] == "feedback"]
    assert [e["payload"]["source"] for e in fb] == ["checks", "checks"]
    review_reads = [e for e in r.events if e["kind"] == "tool_call" and e["channel"] == "review"]
    assert review_reads and all(e["tool_name"] == "list_orders" for e in review_reads)
    assert r.llm_calls == 5  # the checks themselves use no model
    assert r.tool_calls == 3 + len(review_reads)


def test_independent_checks_correct_a_stale_answer(urls, faults):
    faults([{"type": "stale_data", "target": "search_inventory"}])

    def recheck(messages):
        return say(re.search(r"a re-check gives (\d+)", messages[-1]["text"]).group(1))

    r, _ = trial(urls, "single+feedback=checks_independent", "t1_stock_lookup", [
        call("search_inventory", sku="A-22"),
        lambda m: say(str(json.loads(m[-1]["results"][0]["body"])["qty"])),
        recheck,
    ])
    assert r.outcome == "pass", r.reason
    assert r.feedback["first_pass"]["outcome"] == "fail_wrong_answer"
    assert [x["verdict"] for x in r.feedback["reviews"]] == ["revise", "ok"]  # a fact is reported once
    truth = [e for e in r.events if e["channel"] == "truth" and e["kind"] == "tool_result"]
    assert truth and not any(e["fault_injected"] for e in truth)


def test_independent_critic_reads_ground_truth(urls, faults):
    faults([{"type": "stale_data", "target": "search_inventory"}])
    r, backend = trial(urls, "single+feedback=critic_independent", "t1_stock_lookup", [
        call("search_inventory", sku="A-22"),
        say("38"),
        call("search_inventory", sku="A-22"),
        say("VERDICT: OK"),
    ])
    reviewer_reads = [e for e in r.events if e["kind"] == "tool_result" and e["channel"] == "truth"]
    assert len(reviewer_reads) == 1 and not reviewer_reads[0]["fault_injected"]
    assert backend.requests[2]["system"].startswith("You review the work")


def test_no_feedback_no_checkpoint(urls):
    r, _ = trial(urls, "single", "t1_stock_lookup", [say("11")])
    assert r.feedback is None
    assert all(e["kind"] != "checkpoint" for e in r.events)


def test_report_shows_what_feedback_did(tmp_path):
    conn = store.connect(tmp_path / "f.db")
    sid = store.start_sweep(conn, "fb", "m", 1)

    def result(first, final, verdicts, rep):
        return TrialResult(
            task_id="t1_stock_lookup", architecture_id="critic", outcome=final, reason="", tool_calls=4,
            tool_errors=0, retries=0, llm_calls=3, tokens_used=300, duration_ms=1, fault_fired=False,
            answer="1", events=[],
            feedback={"option": "critic", "first_pass": {"outcome": first, "tool_calls": 1, "tokens": 100},
                      "reviews": [{"round": i, "verdict": v} for i, v in enumerate(verdicts)]},
        ), rep

    for r, rep in [
        result("fail_wrong_answer", "pass", ["revise", "ok"], 0),   # fixed
        result("pass", "fail_wrong_answer", ["revise", "ok"], 1),   # false alarm that broke it
        result("fail_wrong_answer", "fail_wrong_answer", ["ok"], 2),  # missed
        result("pass", "pass", ["ok"], 3),
    ]:
        store.record(conn, sid, "none", rep, 1, r)
    (fb,) = report.summarize(conn, "fb")["feedback"]
    assert {k: fb[k] for k in ("n", "first_pass", "final", "fixed", "broke", "flagged", "false_alarms", "missed")} \
        == {"n": 4, "first_pass": 2, "final": 2, "fixed": 1, "broke": 1, "flagged": 2, "false_alarms": 1, "missed": 1}
    assert fb["extra_tool_calls"] == 3 and fb["extra_tokens"] == 200
    assert "Feedback: first answer vs final" in report.build(conn, "fb")
    stored = store.get_trial(conn, store.list_trials(conn, sid)[0]["id"])
    assert stored["feedback"]["option"] == "critic"
