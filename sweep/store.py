import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "faultline.db"

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


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now():
    return datetime.now(timezone.utc).isoformat()


def start_sweep(conn, label: str, model_id: str, repeats: int) -> str:
    row = conn.execute("select id from sweep where label = ?", (label,)).fetchone()
    if row:
        return row["id"]
    sweep_id = str(uuid.uuid4())
    conn.execute(
        "insert into sweep (id, label, model_id, repeats_per_cell, started_at, status) values (?,?,?,?,?,?)",
        (sweep_id, label, model_id, repeats, now(), "running"),
    )
    conn.commit()
    return sweep_id


def finish_sweep(conn, sweep_id: str):
    conn.execute("update sweep set status = ? where id = ?", ("done", sweep_id))
    conn.commit()


def done_cells(conn, sweep_id: str) -> set:
    rows = conn.execute(
        "select task_id, architecture_id, fault_config, repeat_idx from trial where sweep_id = ?",
        (sweep_id,),
    ).fetchall()
    return {(r["task_id"], r["architecture_id"], r["fault_config"], r["repeat_idx"]) for r in rows}


def record(conn, sweep_id: str, fault_config: str, repeat_idx: int, seed: int, result):
    trial_id = str(uuid.uuid4())
    conn.execute(
        """insert into trial
           (id, sweep_id, task_id, architecture_id, fault_config, repeat_idx, seed,
            outcome, reason, fault_fired, tool_calls, retries, tokens_used, answer, created_at)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            trial_id, sweep_id, result.task_id, result.architecture_id, fault_config,
            repeat_idx, seed, result.outcome, result.reason, int(result.fault_fired),
            result.tool_calls, result.retries, result.tokens_used, result.answer, now(),
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