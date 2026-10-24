import time

import pytest

from agents.llm import Reply, ScriptedBackend, ToolCall
from agents.registry import ablation as registry_ablation
from agents.registry import resolve
from harness import BadHarness, ablation, parse
from runner.trial import TrialResult, run_trial
from sweep import analysis, attribution, report, run, store
from tasks.catalog import BY_ID

ORDER = {"customer_id": "CUST-100", "sku": "A-22", "qty": 5}


# ---- ablation sets -------------------------------------------------------------

def test_ablating_full_gives_add_one_and_leave_one_out():
    names = ablation("full")
    assert names == [
        "single",
        "single+instructions=fault_aware", "single+tools=retrying", "single+environment=idempotent",
        "single+state=ledger", "single+feedback=checks",
        "full-instructions", "full-tools", "full-environment", "full-state", "full-feedback",
        "full",
    ]
    full = parse("full")
    for slot in ("instructions", "tools", "environment", "state", "feedback"):
        assert parse(f"full-{slot}").differs(full) == [slot]
        assert parse(f"single+{slot}={getattr(full, slot)}").differs(parse("single")) == [slot]
    with pytest.raises(BadHarness, match="nothing to ablate"):
        ablation("single")


def test_scripted_controls_ablate_around_the_oracle():
    # with two subsystems changed, leave-one-out and add-one are the same harnesses: kept once
    assert registry_ablation("oracle_retry+environment=idempotent") == [
        "oracle", "oracle+tools=retrying", "oracle+environment=idempotent", "oracle_retry+environment=idempotent",
    ]
    assert resolve("oracle_retry+environment=idempotent-tools").spec == resolve("oracle+environment=idempotent").spec
    assert registry_ablation("oracle_retry") == ["oracle", "oracle_retry"]


def test_sweep_architecture_choice():
    assert run.choose_archs(None, None) == ["single", "retry", "critic"]
    assert run.choose_archs(["critic"], "retry") == ["critic", "single", "retry"]
    assert len(run.choose_archs(None, "full")) == 12


def test_ablation_sweep_end_to_end(stack, tmp_path, capsys):
    db = str(tmp_path / "a.db")
    run.main(["--label", "abl", "--ablate", "oracle_retry+environment=idempotent", "--serve", "--db", db,
              "--repeats", "1", "--configs", "none", "--tasks", "t1_stock_lookup", "t2_simple_order"])
    assert "8 trials to run" in capsys.readouterr().out
    s = report.summarize(store.connect(db), "abl")
    assert set(s["archs"]) == {"oracle", "oracle+tools=retrying", "oracle+environment=idempotent",
                               "oracle_retry+environment=idempotent"}
    assert {c["slot"] for c in s["comparisons"]} == {"tools", "environment"} and len(s["comparisons"]) == 4
    assert all(c["n"] == 2 and c["delta"] == 0 for c in s["comparisons"])


# ---- controlled comparisons ----------------------------------------------------

def row(arch, config, rep, passed, tokens=0, task="t2_simple_order"):
    return {"architecture_id": arch, "task_id": task, "fault_config": config, "repeat_idx": rep,
            "outcome": "pass" if passed else "fail_wrong_answer", "tokens_used": tokens, "tool_calls": 1,
            "llm_calls": 0}


def synthetic():
    # timeouts on writes: retries only help once writes are idempotent
    rows = []
    for rep in range(4):
        rows += [
            row("oracle", "timeout", rep, False),
            row("oracle_retry", "timeout", rep, rep == 0),
            row("oracle+environment=idempotent", "timeout", rep, False),
            row("oracle_retry+environment=idempotent", "timeout", rep, True),
            row("oracle", "none", rep, True),
            row("oracle_retry", "none", rep, True),
            row("oracle+environment=idempotent", "none", rep, True),
            row("oracle_retry+environment=idempotent", "none", rep, True),
        ]
    return rows


def test_comparisons_pair_one_subsystem_differences_by_cell():
    rows = analysis.comparisons(synthetic(), ["none", "timeout"])
    pairs = {(r["slot"], r["from_arch"], r["to_arch"]): r for r in rows}
    assert set(pairs) == {
        ("tools", "oracle", "oracle_retry"),
        ("tools", "oracle+environment=idempotent", "oracle_retry+environment=idempotent"),
        ("environment", "oracle", "oracle+environment=idempotent"),
        ("environment", "oracle_retry", "oracle_retry+environment=idempotent"),
    }
    env_with_retries = pairs[("environment", "oracle_retry", "oracle_retry+environment=idempotent")]
    assert (env_with_retries["from_option"], env_with_retries["to_option"]) == ("bare", "idempotent")
    assert env_with_retries["n"] == 8
    assert env_with_retries["by_config"]["timeout"] == {"delta": 75.0, "n": 4}
    assert env_with_retries["by_config"]["none"]["delta"] == 0
    assert pairs[("environment", "oracle", "oracle+environment=idempotent")]["delta"] == 0


def test_interactions_are_flagged_when_contexts_disagree():
    found = analysis.interactions(analysis.comparisons(synthetic(), ["none", "timeout"]))
    by = {(x["slot"], x["config"]): x for x in found}
    assert set(by) == {("tools", "all"), ("tools", "timeout"), ("environment", "all"), ("environment", "timeout")}
    env = by[("environment", "timeout")]
    assert env["contexts"][0]["from_arch"] == "oracle_retry" and env["gap"] == pytest.approx(75)
    assert by[("environment", "all")]["gap"] == pytest.approx(37.5)


def test_aliases_are_compared_once():
    rows = synthetic() + [{**r, "architecture_id": "oracle_retry+environment=idempotent-environment"}
                          for r in synthetic() if r["architecture_id"] == "oracle_retry"]
    tools = [c for c in analysis.comparisons(rows, ["none", "timeout"]) if c["slot"] == "tools"]
    assert len(tools) == 2 and ("oracle", "oracle_retry") in {(c["from_arch"], c["to_arch"]) for c in tools}


def test_spread_ignores_scripted_controls():
    cells = [
        {"config": "none", "arch": "single", "passes": 9, "n": 10},
        {"config": "none", "arch": "retry", "passes": 10, "n": 10},
        {"config": "none", "arch": "oracle", "passes": 0, "n": 10},
        {"config": "timeout", "arch": "single", "passes": 2, "n": 10},
        {"config": "timeout", "arch": "retry", "passes": 8, "n": 10},
    ]
    s = {r["config"]: r for r in analysis.spread(cells, ["none", "timeout"])}
    assert (s["timeout"]["best"], s["timeout"]["worst"], round(s["timeout"]["spread"])) == ("retry", "single", 60)
    assert s["none"]["worst"] == "single"
    assert s["all"]["best"] == "retry" and round(s["all"]["spread"]) == 35


# ---- failure attribution ---------------------------------------------------------

def oracle(urls, task_id, arch="oracle"):
    toolserver_url, proxy_url = urls
    return run_trial(BY_ID[task_id], arch, toolserver_url=toolserver_url, proxy_url=proxy_url)


def test_passing_trials_have_no_attribution(urls):
    assert oracle(urls, "t1_stock_lookup").attribution is None


def test_trusted_bad_data(urls, faults):
    faults([{"type": "stale_data", "target": "search_inventory"}])
    a = oracle(urls, "t1_stock_lookup").attribution
    assert (a["cause"], a["subsystem"]) == ("trusted_bad_data", "feedback")
    assert "the answer uses" in a["evidence"]


def test_gave_up_on_errors(urls, faults):
    faults([{"type": "rate_limit", "target": "search_inventory", "params": {"retry_after": 0}}])
    a = oracle(urls, "t1_stock_lookup").attribution
    assert (a["cause"], a["subsystem"]) == ("gave_up_on_errors", "tools")


def test_lost_write(urls, faults):
    faults([{"type": "timeout", "target": "place_order", "max_fires": 3, "params": {"delay_s": 0}}])
    a = oracle(urls, "t2_simple_order", "oracle_retry").attribution
    assert (a["cause"], a["subsystem"]) == ("lost_write", "tools") and "environment" in a["also"]


def scripted(urls, arch, task_id, steps, max_steps=12):
    toolserver_url, proxy_url = urls
    return run_trial(BY_ID[task_id], arch, ScriptedBackend(steps), max_steps, toolserver_url, proxy_url)


def test_duplicate_after_an_ambiguous_write(urls, faults):
    faults([{"type": "partial_write", "target": "place_order", "max_fires": 1}])
    order = Reply(calls=[ToolCall("place_order", ORDER)])
    a = scripted(urls, "single", "t2_simple_order", [order, order, Reply(text="done")]).attribution
    assert (a["cause"], a["subsystem"]) == ("duplicate_write", "state")
    assert "ORD-0001, ORD-0002" in a["evidence"]


def test_reasoning_error_and_loop(urls):
    a = scripted(urls, "single", "t1_stock_lookup", [Reply(text="99")]).attribution
    assert (a["cause"], a["subsystem"]) == ("reasoning_error", "instructions") and "clean data" in a["evidence"]
    call = Reply(calls=[ToolCall("search_inventory", {"sku": "A-22"})])
    a = scripted(urls, "single", "t1_stock_lookup", [call] * 2, max_steps=2).attribution
    assert (a["cause"], a["subsystem"]) == ("loop", "state")


def test_feedback_that_breaks_a_passing_answer():
    trial = {"outcome": "fail_wrong_answer", "reason": "x", "events": [], "world_diff": {},
             "feedback": {"option": "critic", "first_pass": {"outcome": "pass"},
                          "reviews": [{"round": 0, "verdict": "revise"}, {"round": 1, "verdict": "ok"}]}}
    a = attribution.attribute(trial)
    assert (a["cause"], a["subsystem"]) == ("feedback_broke_it", "feedback")


def test_missed_failures_are_noted():
    trial = {"outcome": "fail_wrong_answer", "reason": "x", "events": [], "world_diff": {},
             "feedback": {"option": "checks", "first_pass": {"outcome": "fail_wrong_answer"},
                          "reviews": [{"round": 0, "verdict": "ok"}]}}
    assert attribution.attribute(trial)["notes"] == ["checks feedback approved the failing first answer"]


def test_report_and_backfill(urls, faults, tmp_path, capsys):
    conn = store.connect(tmp_path / "r.db")
    sid = store.start_sweep(conn, "attr", "none", 1)
    faults([{"type": "stale_data", "target": "search_inventory"}])
    r = oracle(urls, "t1_stock_lookup")
    store.record(conn, sid, "stale_data", 0, 1, r)
    conn.execute("update trial set attribution = null")
    conn.commit()
    attribution.main(["--label", "attr", "--db", str(tmp_path / "r.db")])
    assert "attributed 1 failed trials" in capsys.readouterr().out
    text = report.build(conn, "attr")
    assert "Failure attribution: cause" in text and "trusted_bad_data" in text
    assert store.list_trials(conn, sid)[0]["attribution"]["subsystem"] == "feedback"


def test_report_shows_comparisons(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    sid = store.start_sweep(conn, "cmp", "none", 1)
    for x in synthetic():
        store.record(conn, sid, x["fault_config"], x["repeat_idx"], 1, TrialResult(
            task_id=x["task_id"], architecture_id=x["architecture_id"], outcome=x["outcome"], reason="",
            tool_calls=1, tool_errors=0, retries=0, llm_calls=0, tokens_used=0, duration_ms=1,
            fault_fired=False, answer="", events=[]))
    text = report.build(conn, "cmp")
    assert "Controlled comparisons" in text and "bare -> idempotent" in text
    assert "interaction under timeout: environment bare -> idempotent depends on the rest of the harness" in text


def test_api_expands_ablations(make_client):
    client = make_client()
    names = [a["id"] for a in client.get("/api/ablation/oracle_retry").json()]
    assert names == ["oracle", "oracle_retry"]
    assert client.get("/api/ablation/single").status_code == 400
    body = {"label": "abl", "ablate": "oracle_retry", "configs": ["none"], "tasks": ["t1_stock_lookup"],
            "repeats": 1, "pace": 0}
    job = client.post("/api/sweeps", json=body).json()
    assert job["params"]["archs"] == names
    deadline = time.monotonic() + 30  # let it finish: the stack is shared with the other tests
    while client.get(f"/api/jobs/{job['id']}").json()["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client.get("/api/sweeps/abl").json()["graded"] == 2
    assert client.post("/api/sweeps", json={**body, "label": "x", "ablate": None}).status_code == 400
