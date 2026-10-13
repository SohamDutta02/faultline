import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("FAULTLINE_DB", Path(__file__).parent.parent / "faultline.db"))

SCHEMA = """
create table if not exists sweep (
    id text primary key,
    label text,
    model_id text,
    repeats_per_cell integer,
    started_at text,
    status text
);

create table if not exists trial (
    id text primary key,
    sweep_id text,
    task_id text,
    architecture_id text,
    fault_config text,
    repeat_idx integer,
    seed integer,
    outcome text,
    reason text,
    fault_fired integer,
    tool_calls integer,
    retries integer,
    tokens_used integer,
    answer text,
    created_at text,
    unique (sweep_id, task_id, architecture_id, fault_config, repeat_idx)
);

create table if not exists trial_event (
    id integer primary key autoincrement,
    trial_id text,
    step_idx integer,
    kind text,
    tool_name text,
    fault_injected integer,
    latency_ms integer,
    payload text
);

create index if not exists idx_event_trial on trial_event(trial_id);
"""

# columns added after the first sweeps were recorded; old databases get them on connect
MIGRATIONS = {
    "trial": {"tool_errors": "integer", "llm_calls": "integer", "duration_ms": "integer"},
}


def connect(path: str | Path | None = None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"pragma table_info({table})")}
        for col, kind in cols.items():
            if col not in have:
                conn.execute(f"alter table {table} add column {col} {kind}")
    conn.commit()
    return conn


def now():
    return datetime.now(timezone.utc).isoformat()


def find_sweep(conn, label: str):
    return conn.execute("select * from sweep where label = ?", (label,)).fetchone()


def start_sweep(conn, label: str, model_id: str, repeats: int) -> str:
    row = find_sweep(conn, label)
    if row:
        conn.execute("update sweep set status = ? where id = ?", ("running", row["id"]))
        conn.commit()
        return row["id"]
    sweep_id = str(uuid.uuid4())
    conn.execute(
        "insert into sweep (id, label, model_id, repeats_per_cell, started_at, status) values (?,?,?,?,?,?)",
        (sweep_id, label, model_id, repeats, now(), "running"),
    )
    conn.commit()
    return sweep_id


def finish_sweep(conn, sweep_id: str, status: str = "done"):
    conn.execute("update sweep set status = ? where id = ?", (status, sweep_id))
    conn.commit()


def done_cells(conn, sweep_id: str) -> set:
    """Cells with a usable result. Harness failures (model API errors) are not
    the agent's doing, so they stay pending and are re-run on resume."""
    rows = conn.execute(
        """select task_id, architecture_id, fault_config, repeat_idx from trial
           where sweep_id = ? and outcome != 'fail_harness'""",
        (sweep_id,),
    ).fetchall()
    return {(r["task_id"], r["architecture_id"], r["fault_config"], r["repeat_idx"]) for r in rows}


def record(conn, sweep_id: str, fault_config: str, repeat_idx: int, seed: int, result):
    stale = conn.execute(
        """select id from trial where sweep_id = ? and task_id = ? and architecture_id = ?
           and fault_config = ? and repeat_idx = ?""",
        (sweep_id, result.task_id, result.architecture_id, fault_config, repeat_idx),
    ).fetchall()
    for row in stale:
        conn.execute("delete from trial_event where trial_id = ?", (row["id"],))
        conn.execute("delete from trial where id = ?", (row["id"],))

    trial_id = str(uuid.uuid4())
    conn.execute(
        """insert into trial
           (id, sweep_id, task_id, architecture_id, fault_config, repeat_idx, seed,
            outcome, reason, fault_fired, tool_calls, tool_errors, retries, llm_calls,
            tokens_used, duration_ms, answer, created_at)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            trial_id, sweep_id, result.task_id, result.architecture_id, fault_config,
            repeat_idx, seed, result.outcome, result.reason, int(result.fault_fired),
            result.tool_calls, result.tool_errors, result.retries, result.llm_calls,
            result.tokens_used, result.duration_ms, result.answer, now(),
        ),
    )
    conn.executemany(
        """insert into trial_event
           (trial_id, step_idx, kind, tool_name, fault_injected, latency_ms, payload)
           values (?,?,?,?,?,?,?)""",
        [
            (
                trial_id, e["step_idx"], e["kind"], e.get("tool_name"),
                int(e.get("fault_injected", False)), e.get("latency_ms", 0),
                json.dumps(e.get("payload", {}))[:4000],
            )
            for e in result.events
        ],
    )
    conn.commit()
    return trial_id
