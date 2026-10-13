import sqlite3

import pytest

from runner.stack import free_port
from runner.trial import TrialResult
from sweep import report, run, store


def result(outcome="pass", task_id="t1_stock_lookup", arch="single"):
    return TrialResult(
        task_id=task_id, architecture_id=arch, outcome=outcome, reason="", tool_calls=1,
        tool_errors=0, retries=0, llm_calls=2, tokens_used=30, duration_ms=5,
        fault_fired=False, answer="11", events=[{"step_idx": 0, "kind": "tool_call", "payload": {}}],
    )


def test_harness_failures_are_rerun_and_replaced(tmp_path):
    conn = store.connect(tmp_path / "s.db")
    sid = store.start_sweep(conn, "x", "m", 1)
    store.record(conn, sid, "none", 0, 1, result("fail_harness"))
    assert store.done_cells(conn, sid) == set()

    store.record(conn, sid, "none", 0, 1, result("pass"))
    assert store.done_cells(conn, sid) == {("t1_stock_lookup", "single", "none", 0)}
    assert conn.execute("select count(*) from trial").fetchone()[0] == 1
    assert conn.execute("select count(*) from trial_event").fetchone()[0] == 1


def test_old_databases_are_migrated(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("create table trial (id text primary key, sweep_id text, outcome text)")
    old.commit()
    old.close()
    cols = {r["name"] for r in store.connect(path).execute("pragma table_info(trial)")}
    assert {"tool_errors", "llm_calls", "duration_ms"} <= cols


def test_cell_seeds_differ_by_task_but_not_architecture():
    assert run.cell_seed("t1", "none", 0) == run.cell_seed("t1", "none", 0)
    assert run.cell_seed("t1", "none", 0) != run.cell_seed("t2", "none", 0)
    assert run.cell_seed("t1", "none", 0) != run.cell_seed("t1", "none", 1)


def test_preflight_rejects_a_missing_proxy(urls):
    toolserver_url, _ = urls
    with pytest.raises(SystemExit, match="no fault proxy"):
        run.preflight(toolserver_url, f"http://127.0.0.1:{free_port()}")


def test_oracle_sweep_end_to_end(stack, tmp_path, capsys):
    db = str(tmp_path / "e2e.db")
    args = ["--label", "e2e", "--archs", "oracle", "--serve", "--db", db, "--repeats", "1",
            "--configs", "none", "stale_data"]
    run.main(args)
    assert "12 trials to run" in capsys.readouterr().out

    run.main(args)
    assert "0 trials to run (12 already done)" in capsys.readouterr().out

    conn = store.connect(db)
    text = report.build(conn, "e2e", by_task=True)
    assert "100% (6)" in text            # oracle is perfect without faults
    assert "stale_data" in text and "Change vs no-fault baseline" in text
    assert "never fired" not in text
    md = report.build(conn, "e2e", markdown=True)
    assert "| config | oracle |" in md


def test_report_warns_when_faults_never_fire(tmp_path):
    conn = store.connect(tmp_path / "r.db")
    sid = store.start_sweep(conn, "w", "m", 1)
    store.record(conn, sid, "none", 0, 1, result())
    store.record(conn, sid, "rate_limit", 0, 1, result())
    assert "rate_limit never fired" in report.build(conn, "w")
