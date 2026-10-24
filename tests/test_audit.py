import copy
import json

import pytest

from agents.registry import resolve
from agents.toolbox import SPECS
from harness import audit, changed_parts
from harness.base import Option
from runner.trial import TrialResult
from sweep import compare, report, run, store, versions


def test_the_current_harness_passes_the_static_audit():
    findings = audit.run()
    assert [f for f in findings if f.level != "info"] == []
    assert any(f.check == "dead_options" and "checks_independent" in f.message for f in findings)


def test_tool_spec_drift_is_caught():
    specs = copy.deepcopy(SPECS)
    get_pricing = next(s for s in specs if s["name"] == "get_pricing")
    get_pricing["input_schema"]["properties"]["level"] = get_pricing["input_schema"]["properties"].pop("tier")
    specs = [s for s in specs if s["name"] != "cancel_order"]
    messages = [f.message for f in audit.check_tool_specs(specs)]
    assert any("get_pricing: the model is offered ['level', 'sku'], the route takes ['sku', 'tier']" in m
               for m in messages)
    assert any("serves /tools/cancel_order but no tool spec offers it" in m for m in messages)


def test_prompts_naming_missing_tools_are_caught():
    options = {"instructions": {"x": Option("x", "d", {"system": "Call get_inventory, then list_orders."})}}
    (finding,) = audit.check_prompt_refs(options)
    assert finding.level == "error" and "get_inventory" in finding.message


def test_manifest_diffs_name_the_parts_that_changed():
    old, new = resolve("single").manifest(), resolve("retry+state=ledger").manifest()
    assert changed_parts(old, new) == ["tools", "state"]
    assert resolve("single").code()["tools"] and resolve("single").code()["instructions"] is None


# ---- results recorded under older harnesses ------------------------------------

def result(arch="oracle", outcome="pass", record=None, task="t1_stock_lookup"):
    return TrialResult(task_id=task, architecture_id=arch, outcome=outcome, reason="", tool_calls=1, tool_errors=0,
                       retries=0, llm_calls=0, tokens_used=0, duration_ms=1, fault_fired=False, answer="1",
                       events=[], harness=record or resolve(arch).record())


def older(arch="oracle", part="tools"):
    """A record of `arch` as it was before someone edited its `part`."""
    rec = copy.deepcopy(resolve(arch).record())
    rec["manifest"][part]["content"] = {"edited": "before"}
    rec["fingerprint"] = "0ld0ld0ld0ld"
    return rec


def test_staleness_and_the_report_warning(tmp_path):
    conn = store.connect(tmp_path / "v.db")
    sid = store.start_sweep(conn, "v", "none", 1)
    store.record(conn, sid, "none", 0, 1, result(record=older()))
    store.record(conn, sid, "none", 1, 1, result())
    (s,) = versions.staleness(conn, sid)
    assert (s["stale"], s["versions"], s["changed"]) == (1, 2, ["tools"])
    text = report.build(conn, "v")
    assert "warning: oracle: 1 trials come from an older harness (tools changed since); results mix 2" in text


def test_resuming_refuses_to_mix_harness_versions(stack, tmp_path, capsys):
    db = str(tmp_path / "r.db")
    args = ["--label", "r", "--archs", "oracle", "--serve", "--db", db, "--repeats", "1",
            "--configs", "none", "--tasks", "t1_stock_lookup"]
    run.main(args)
    conn = store.connect(db)
    store.remember_harness(conn, "oracle", older())
    conn.execute("update trial set harness_fp = '0ld0ld0ld0ld'")
    conn.commit()
    capsys.readouterr()

    with pytest.raises(run.StaleHarness, match="--rerun-stale"):
        run.main(args)
    assert "harness changed: oracle: 1 trials come from an older harness (tools changed since)" in \
        capsys.readouterr().out
    run.main(args + ["--allow-mixed"])
    assert "0 trials to run" in capsys.readouterr().out
    run.main(args + ["--rerun-stale"])
    assert "1 trials to run" in capsys.readouterr().out
    assert versions.staleness(store.connect(db), store.find_sweep(conn, "r")["id"])[0]["stale"] == 0


def test_audit_flags_stale_results_and_unbumped_code(tmp_path):
    conn = store.connect(tmp_path / "a.db")
    sid = store.start_sweep(conn, "a", "none", 1)
    store.record(conn, sid, "none", 0, 1, result(record=older("oracle_retry"), arch="oracle_retry"))
    rec = copy.deepcopy(resolve("oracle").record())
    rec["code"]["tools"] = "c0dec0dec0de"  # same manifest, different code: no version bump
    store.record(conn, sid, "none", 0, 1, result(record=rec))
    found = {f.check: f for f in audit.check_recorded(conn)}
    assert "oracle_retry: the latest results (sweep a) come from an older harness (tools changed)" in \
        found["stale_results"].message
    assert found["unbumped_code"].message.startswith("tools=plain: its code changed since")


def test_audit_cli_exit_codes(tmp_path, capsys):
    with pytest.raises(SystemExit) as clean:
        audit.main(["--db", str(tmp_path / "empty.db")])
    assert clean.value.code == 0
    conn = store.connect(tmp_path / "stale.db")
    sid = store.start_sweep(conn, "s", "none", 1)
    store.record(conn, sid, "none", 0, 1, result(record=older()))
    with pytest.raises(SystemExit) as lenient:
        audit.main(["--db", str(tmp_path / "stale.db")])
    with pytest.raises(SystemExit) as strict:
        audit.main(["--db", str(tmp_path / "stale.db"), "--strict"])
    assert (lenient.value.code, strict.value.code) == (0, 1)
    assert "stale_results" in capsys.readouterr().out


def test_calibration_verifies_the_verifier(stack):
    findings = audit.check_calibration()
    assert [f.level for f in findings] == ["info"]
    assert "0 false alarms on 12 correct runs; 4/4 seeded failures caught" in findings[0].message


# ---- comparing sweeps --------------------------------------------------------------

def test_compare_pairs_cells_and_names_what_changed(tmp_path):
    conn = store.connect(tmp_path / "c.db")
    base, head = store.start_sweep(conn, "base", "m", 2), store.start_sweep(conn, "head", "m", 2)
    for rep in range(4):
        store.record(conn, base, "timeout", rep, 1, result(record=older()))
        store.record(conn, head, "timeout", rep, 1, result(outcome="fail_wrong_answer" if rep else "pass"))
        store.record(conn, base, "none", rep, 1, result(arch="oracle_retry"))
        store.record(conn, head, "none", rep, 1, result(arch="oracle_retry"))
    store.record(conn, head, "none", 0, 1, result(arch="oracle+environment=idempotent"))
    c = compare.compare(conn, "base", "head")
    arch = {a["arch"]: a for a in c["archs"]}
    assert arch["oracle"]["delta"] == -75 and arch["oracle"]["harness"] == "changed"
    assert arch["oracle"]["changed"] == ["tools"] and arch["oracle_retry"]["harness"] == "same"
    assert [(r["arch"], r["config"]) for r in c["regressions"]] == [("oracle", "timeout")]
    assert c["only_head"] == ["oracle+environment=idempotent"]
    text = compare.build(conn, "base", "head", markdown=True)
    assert "### Regressions" in text and "changed (tools)" in text
    assert compare.build(conn, "base", "nope") == "no sweep labelled nope"


def test_api_audit_compare_and_stale_resume(make_client, tmp_path):
    client = make_client()
    assert all(f["level"] == "info" for f in client.get("/api/audit").json())

    body = {"label": "s", "archs": ["oracle"], "configs": ["none"], "tasks": ["t1_stock_lookup"],
            "repeats": 1, "pace": 0}
    client.post("/api/sweeps", json=body)
    conn = store.connect(tmp_path / "api.db")
    import time
    deadline = time.monotonic() + 30
    while (store.find_sweep(conn, "s") is None or store.find_sweep(conn, "s")["status"] == "running") \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    store.remember_harness(conn, "oracle", older())
    conn.execute("update trial set harness_fp = '0ld0ld0ld0ld'")
    conn.commit()

    refused = client.post("/api/sweeps", json=body)
    assert refused.status_code == 409 and "older harness" in json.dumps(refused.json())
    assert any(f["check"] == "stale_results" for f in client.get("/api/audit").json())
    assert client.get("/api/compare", params={"base": "s", "head": "s"}).json()["archs"][0]["delta"] == 0
    assert client.get("/api/compare", params={"base": "s", "head": "x"}).status_code == 404
