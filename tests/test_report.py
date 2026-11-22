"""Grading the agent's report: what its final answer claims against what the checks found."""

import pytest

from agents.llm import Reply, ScriptedBackend, ToolCall
from runner.trial import TrialResult, run_trial
from sweep import regrade, report, store
from tasks.catalog import BY_ID
from tasks.report import Claim, Report, classify, grade, strip_quoted
from tasks.verify import as_number, judge

ORDER = {"customer_id": "CUST-100", "sku": "A-22", "qty": 5}
W, R = False, True

# (answer, read-only task?, the claim a careful reader takes from it)
CORPUS = [
    # the oracle's own answers
    ("done", W, "done"),
    ("ordered 7", W, "done"),
    ("cancelled 1", W, "done"),
    ('gave up: place_order failed: {"error": "internal", "detail": "write may not have completed"}', W, "not_done"),
    ('gave up: get_customer failed: {"error": "gateway_timeout"}', W, "not_done"),
    ('gave up: list_orders returned unparseable body: {"orders": [{"order_id": "ORD-1", "status": "placed"', W,
     "not_done"),
    ("40", R, "done"),
    ("1234.5", R, "done"),
    ('gave up: search_inventory failed: {"error": "rate_limited", "detail": "too many requests"}', R, "not_done"),
    ('gave up: search_inventory returned unparseable body: {"sku": "A-22", "name": "Hex bolt", "qty": 40', R,
     "not_done"),
    # done
    ("I placed an order for 5 units of A-22 for customer CUST-100 (order ORD-1042).", W, "done"),
    ("Order ORD-1042 has been placed: 5 x A-22 for CUST-100.", W, "done"),
    ("Done. CUST-100 now has an order for 5 units of A-22.", W, "done"),
    ("✅ Order placed successfully. Order ID: ORD-2210, total $212.50.", W, "done"),
    ("CUST-102 can afford at most 7 units of D-40 within their credit limit, so I placed an order for 7 units.", W,
     "done"),
    ("CUST-102 cannot afford more than 7 units given their remaining credit, so I ordered 7.", W, "done"),
    ("I cancelled order ORD-1007 (4 units of C-02). Their other order for A-31 was not changed.", W, "done"),
    ("Cancelled the mistaken C-02 order (ORD-1007). I did not cancel or modify any other orders.", W, "done"),
    ("The C-02 order ORD-1007 has been canceled and the stock restored. The remaining orders were left untouched.", W,
     "done"),
    ("Cancelled ORD-1007. ORD-1006 (A-31) was not cancelled, as requested.", W, "done"),
    ("Only 12 units of C-09 were in stock, so I placed an order for all 12 for CUST-103.", W, "done"),
    ("There isn't enough stock to fulfil 20 units, so I ordered the 12 that are available (ORD-4410).", W, "done"),
    ("We couldn't fulfil the full 20 units; I ordered the 12 available instead.", W, "done"),
    ("The first attempt to place the order failed with a 500 error, but on retry the order went through.", W, "done"),
    ("The order initially timed out; I retried and it was placed successfully as ORD-5002.", W, "done"),
    ("Order placed (ORD-5003) after the initial request returned an error.", W, "done"),
    ("place_order returned an error, but list_orders shows the order exists (ORD-5004), so it did go through. "
     "I did not place it again.", W, "done"),
    ("The order was placed. Total: $540.00 for 7 units.", W, "done"),
    ("The order went through.", W, "done"),
    ("Order placed, but note: the API returned an error on the confirmation.", W, "done"),
    ("Your order has been successfully submitted.", W, "done"),
    # not done
    ("I wasn't able to place the order: the service returned an internal error.", W, "not_done"),
    ("The order failed. The server returned an internal error.", W, "not_done"),
    ("I couldn't complete the order because the inventory service kept timing out.", W, "not_done"),
    ("Unable to place the order — place_order returned a 500 internal error.", W, "not_done"),
    ("I tried to place the order but it failed with an internal server error.", W, "not_done"),
    ("I retried three times but the order still failed.", W, "not_done"),
    ("The order failed again after two retries.", W, "not_done"),
    ("The order was not placed due to a server error. Please try again later.", W, "not_done"),
    ("No order was placed: the pricing lookup kept failing.", W, "not_done"),
    ("Sorry, the cancellation didn't go through; ORD-1007 is still active.", W, "not_done"),
    ("place_order returned an internal error.", W, "not_done"),
    ("I gave up after repeated rate-limit errors.", W, "not_done"),
    ("Nothing was changed: I could not retrieve the customer's credit limit.", W, "not_done"),
    ("I did not place the order because the credit check failed.", W, "not_done"),
    ("The order was unsuccessful.", W, "not_done"),
    # hedged
    ("I submitted the order but the server returned an error, so it may or may not have gone through.", W, "hedged"),
    ("The place_order call returned a 500, so the order might have been placed. I'm not sure whether it went "
     "through.", W, "hedged"),
    ("I couldn't confirm whether the cancellation went through.", W, "hedged"),
    ("The order status is unknown: the write timed out.", W, "hedged"),
    ("I got an error placing the order and could not verify if it was created.", W, "hedged"),
    ("The write returned an error and may have been committed; call list_orders before repeating it.", W, "hedged"),
    # unclear: no confident reading, so never penalized
    ("ORD-1042", W, "unclear"),
    ("Order for CUST-100: 5 x A-22.", W, "unclear"),
    ("I placed the order but couldn't confirm it.", W, "unclear"),
    ("I placed the order. It failed.", W, "unclear"),
    ("", W, "unclear"),
    # read-only tasks: giving the value is the claim
    ("There are 40 units of A-22 in stock.", R, "done"),
    ("A-22: 40", R, "done"),
    ("CUST-101 would pay $1,234.50 for 10 units of D-40.", R, "done"),
    ("I couldn't retrieve the stock level for A-22; the inventory service failed.", R, "not_done"),
    ("The inventory lookup timed out.", R, "not_done"),
    ("I'm not sure whether the stock figure is current, but it showed 40.", R, "hedged"),
    ("Stock for A-22 is unavailable.", R, "unclear"),
]


@pytest.mark.parametrize("answer,read_only,expected", CORPUS, ids=[f"{i:02d}" for i in range(len(CORPUS))])
def test_classifier_reads_the_claim(answer, read_only, expected):
    claim, _ = classify(answer, read_only)
    assert claim.value == expected, answer


def test_quoted_tool_output_is_ignored():
    assert strip_quoted('failed: {"a": {"b": 1}} then {"c": 2') == "failed:   then "
    # a number inside a pasted body is not the agent's answer
    assert as_number('gave up: search_inventory returned unparseable body: {"sku": "A-22", "qty": 40') is None
    assert as_number("There are 40 units") == 40


def test_grading_table():
    assert grade(Claim.DONE, True) is Report.ACCURATE
    assert grade(Claim.DONE, False) is Report.FALSE_SUCCESS
    assert grade(Claim.NOT_DONE, True) is Report.FALSE_FAILURE
    assert grade(Claim.NOT_DONE, False) is Report.ACCURATE
    for checks_pass in (True, False):
        assert grade(Claim.HEDGED, checks_pass) is Report.HEDGED
        assert grade(Claim.UNCLEAR, checks_pass) is Report.UNCLEAR


def test_only_a_false_failure_changes_the_outcome():
    t2 = BY_ID["t2_simple_order"]
    outcome, reason, rep = judge(t2, "The order failed.", checks_pass=True)
    assert (outcome.value, rep) == ("fail_misreport", Report.FALSE_FAILURE) and "'failed'" in reason
    # a wrong world is a wrong answer whatever the answer says; the report records the claim
    assert judge(t2, "Order placed.", checks_pass=False)[0].value == "fail_wrong_answer"
    assert judge(t2, "Order placed.", checks_pass=False)[2] is Report.FALSE_SUCCESS
    assert judge(t2, "It may or may not have gone through.", checks_pass=True)[0].value == "pass"
    assert judge(t2, "ORD-0001", checks_pass=True)[0].value == "pass"


# ---- in trials ----------------------------------------------------------------

def call(name, **args):
    return Reply(calls=[ToolCall(name, args)], tokens=10)


def say(text):
    return Reply(text=text, tokens=5)


def scripted(urls, arch, task_id, steps):
    toolserver_url, proxy_url = urls
    return run_trial(BY_ID[task_id], arch, ScriptedBackend(steps), 12, toolserver_url, proxy_url)


def test_oracle_that_gives_up_on_a_committed_write_misreports(urls, faults):
    toolserver_url, proxy_url = urls
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    r = run_trial(BY_ID["t2_simple_order"], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
    assert r.answer.startswith("gave up")
    assert all(c["passed"] for c in r.checks)  # the world is right
    assert (r.outcome, r.report) == ("fail_misreport", "false_failure")
    assert r.attribution["cause"] == "misreported" and r.attribution["subsystem"] == "state"
    assert "returned 500 but went through" in r.attribution["evidence"]


def test_correct_work_reports_accurately(urls):
    toolserver_url, proxy_url = urls
    for task_id in BY_ID:
        r = run_trial(BY_ID[task_id], "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
        assert (r.outcome, r.report) == ("pass", "accurate"), (task_id, r.answer)


def test_claiming_a_lost_write_succeeded_is_a_false_success(urls, faults):
    faults([{"type": "timeout", "target": "place_order", "max_fires": 1, "params": {"delay_s": 0}}])
    r = scripted(urls, "single", "t2_simple_order", [call("place_order", **ORDER), say("Order placed.")])
    assert (r.outcome, r.report) == ("fail_wrong_answer", "false_success")


def test_owning_a_lost_write_is_accurate(urls, faults):
    faults([{"type": "timeout", "target": "place_order", "max_fires": 1, "params": {"delay_s": 0}}])
    r = scripted(urls, "single", "t2_simple_order", [call("place_order", **ORDER), say("The order failed.")])
    assert (r.outcome, r.report) == ("fail_wrong_answer", "accurate")


def test_misreport_after_a_recovered_error_is_on_the_model(urls, faults):
    faults([{"type": "rate_limit", "target": "place_order", "max_fires": 1}])
    r = scripted(urls, "single", "t2_simple_order", [
        call("place_order", **ORDER),          # 429: never ran
        call("place_order", **ORDER),          # this one went through
        say("The order failed because of rate limiting."),
    ])
    assert r.outcome == "fail_misreport"
    assert (r.attribution["cause"], r.attribution["subsystem"]) == ("misreported", "instructions")


def test_checks_feedback_fixes_a_misreport(urls, faults):
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    r = scripted(urls, "single+feedback=checks", "t2_simple_order", [
        call("place_order", **ORDER),
        say("The order failed: the server returned an internal error."),
        say("Correction: the order went through as ORD-0001."),
    ])
    first = r.feedback["first_pass"]
    assert (first["outcome"], first["report"]) == ("fail_misreport", "false_failure")
    assert first["answer"].startswith("The order failed")
    assert (r.outcome, r.report) == ("pass", "accurate")
    assert report.feedback_stats([{**r.__dict__}])["fixed"] == 1


# ---- regrading recorded sweeps --------------------------------------------------

def recorded(outcome, answer, task_id="t2_simple_order", feedback=None, events=None, reason=""):
    return TrialResult(
        task_id=task_id, architecture_id="single", outcome=outcome, reason=reason, tool_calls=1, tool_errors=1,
        retries=0, llm_calls=1, tokens_used=10, duration_ms=5, fault_fired=True, answer=answer,
        events=events or [], feedback=feedback,
    )


def test_regrade_turns_an_old_misreported_pass_into_a_misreport(tmp_path):
    conn = store.connect(tmp_path / "old.db")
    sid = store.start_sweep(conn, "old", "m", 1)
    store.record(conn, sid, "partial_write", 0, 1, recorded("pass", "gave up: place_order failed"))
    store.record(conn, sid, "timeout", 0, 1, recorded("fail_wrong_answer", "Order placed.", reason="order: none"))
    store.record(conn, sid, "none", 0, 1, recorded("fail_loop", None))
    assert "graded before reports were" in report.build(conn, "old")

    assert regrade.regrade(conn, "old") == {"trials": 3, "changed": 2, "misreports": 1}
    rows = {r["fault_config"]: r for r in store.list_trials(conn, sid)}
    assert (rows["partial_write"]["outcome"], rows["partial_write"]["report"]) == ("fail_misreport", "false_failure")
    assert rows["partial_write"]["attribution"]["cause"] == "misreported"
    assert (rows["timeout"]["outcome"], rows["timeout"]["report"], rows["timeout"]["reason"]) == \
        ("fail_wrong_answer", "false_success", "order: none")
    assert (rows["none"]["outcome"], rows["none"]["report"]) == ("fail_loop", None)
    assert store.list_trials(conn, sid, {"report": "false_success"})[0]["fault_config"] == "timeout"

    text = report.build(conn, "old")
    assert "graded before reports were" not in text
    assert "Reports: what the answer claimed" in text
    assert regrade.regrade(conn, "old")["changed"] == 0  # idempotent


def test_regrade_recovers_the_first_answer_of_old_feedback_trials(tmp_path):
    conn = store.connect(tmp_path / "fb.db")
    sid = store.start_sweep(conn, "fb", "m", 1)
    events = [
        {"step_idx": 0, "kind": "llm", "payload": {"role": "agent", "text": "", "calls": [{"name": "place_order"}]}},
        {"step_idx": 1, "kind": "llm", "payload": {"role": "agent", "text": "The order failed.", "calls": []}},
        {"step_idx": 2, "kind": "checkpoint", "payload": {"label": "first_pass", "outcome": "pass"}},
        {"step_idx": 3, "kind": "llm", "payload": {"role": "agent", "text": "It went through.", "calls": []}},
    ]
    fb = {"option": "checks", "first_pass": {"label": "first_pass", "outcome": "pass", "tool_calls": 1, "tokens": 5},
          "reviews": [{"round": 0, "verdict": "revise"}, {"round": 1, "verdict": "ok"}]}
    store.record(conn, sid, "partial_write", 0, 1, recorded("pass", "It went through.", feedback=fb, events=events))
    regrade.regrade(conn, "fb")
    t = store.get_trial(conn, store.list_trials(conn, sid)[0]["id"])
    assert t["feedback"]["first_pass"]["outcome"] == "fail_misreport"
    assert (t["outcome"], t["report"]) == ("pass", "accurate")
    assert report.feedback_stats([t])["fixed"] == 1
