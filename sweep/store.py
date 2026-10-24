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
    "sweep": {"params": "text"},
    "trial": {
        "tool_errors": "integer",
        "llm_calls": "integer",
        "duration_ms": "integer",
        "checks": "text",
        "world_diff": "text",
        "feedback": "text",
        "attribution": "text",
    },
    "trial_event": {"channel": "text"},
}


def connect(path: str | Path | None = None, check_same_thread: bool = True):
    conn = sqlite3.connect(path or DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")  # readers don't block the sweep writer
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


def start_sweep(conn, label: str, model_id: str, repeats: int, params: dict | None = None) -> str:
    """Create a sweep, or reopen it to resume. params records the planned matrix
    (archs, configs, tasks, repeats) so progress can be shown against it."""
    row = find_sweep(conn, label)
    if row:
        conn.execute("update sweep set status = ? where id = ?", ("running", row["id"]))
        if params is not None:
            conn.execute("update sweep set params = ? where id = ?", (json.dumps(params), row["id"]))
        conn.commit()
        return row["id"]
    sweep_id = str(uuid.uuid4())
    conn.execute(
        "insert into sweep (id, label, model_id, repeats_per_cell, started_at, status, params) values (?,?,?,?,?,?,?)",
        (sweep_id, label, model_id, repeats, now(), "running", json.dumps(params) if params else None),
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
            tokens_used, duration_ms, answer, checks, world_diff, feedback, attribution, created_at)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            trial_id, sweep_id, result.task_id, result.architecture_id, fault_config,
            repeat_idx, seed, result.outcome, result.reason, int(result.fault_fired),
            result.tool_calls, result.tool_errors, result.retries, result.llm_calls,
            result.tokens_used, result.duration_ms, result.answer,
            json.dumps(result.checks), json.dumps(result.world_diff),
            json.dumps(result.feedback) if getattr(result, "feedback", None) else None,
            json.dumps(result.attribution) if getattr(result, "attribution", None) else None, now(),
        ),
    )
    conn.executemany(
        """insert into trial_event
           (trial_id, step_idx, kind, tool_name, fault_injected, latency_ms, payload, channel)
           values (?,?,?,?,?,?,?,?)""",
        [
            (
                trial_id, e["step_idx"], e["kind"], e.get("tool_name"),
                int(e.get("fault_injected", False)), e.get("latency_ms", 0),
                json.dumps(e.get("payload", {}))[:4000], e.get("channel") or "agent",
            )
            for e in result.events
        ],
    )
    conn.commit()
    return trial_id


def next_repeat(conn, sweep_id: str, task_id: str, architecture_id: str, fault_config: str) -> int:
    row = conn.execute(
        """select max(repeat_idx) as m from trial
           where sweep_id = ? and task_id = ? and architecture_id = ? and fault_config = ?""",
        (sweep_id, task_id, architecture_id, fault_config),
    ).fetchone()
    return 0 if row["m"] is None else row["m"] + 1


def list_sweeps(conn) -> list[dict]:
    rows = conn.execute(
        """select s.*, count(t.id) as trials,
                  sum(t.outcome = 'pass') as passes,
                  sum(t.outcome = 'fail_harness') as harness,
                  max(t.created_at) as last_trial_at
           from sweep s left join trial t on t.sweep_id = s.id
           group by s.id order by s.started_at desc"""
    ).fetchall()
    return [_with_params(dict(r)) for r in rows]


def _with_params(sweep: dict) -> dict:
    sweep["params"] = json.loads(sweep["params"]) if sweep.get("params") else None
    return sweep


def list_trials(conn, sweep_id: str, filters: dict | None = None, limit: int = 500) -> list[dict]:
    where, params = ["sweep_id = ?"], [sweep_id]
    for col in ("fault_config", "architecture_id", "task_id", "outcome"):
        val = (filters or {}).get(col)
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    rows = conn.execute(
        f"""select id, task_id, architecture_id, fault_config, repeat_idx, seed, outcome, reason,
                   fault_fired, tool_calls, tool_errors, retries, llm_calls, tokens_used,
                   duration_ms, answer, attribution, created_at
            from trial where {' and '.join(where)}
            order by created_at desc limit ?""",
        params + [limit],
    ).fetchall()
    return [{**dict(r), "attribution": json.loads(r["attribution"]) if r["attribution"] else None} for r in rows]


def get_trial(conn, trial_id: str) -> dict | None:
    row = conn.execute("select * from trial where id = ?", (trial_id,)).fetchone()
    if row is None:
        return None
    trial = dict(row)
    trial["checks"] = json.loads(trial["checks"]) if trial.get("checks") else []
    trial["world_diff"] = json.loads(trial["world_diff"]) if trial.get("world_diff") else None
    trial["feedback"] = json.loads(trial["feedback"]) if trial.get("feedback") else None
    trial["attribution"] = json.loads(trial["attribution"]) if trial.get("attribution") else None
    sweep = conn.execute("select label from sweep where id = ?", (trial["sweep_id"],)).fetchone()
    trial["sweep_label"] = sweep["label"] if sweep else None
    events = conn.execute(
        "select step_idx, kind, tool_name, fault_injected, latency_ms, payload, channel from trial_event "
        "where trial_id = ? order by step_idx",
        (trial_id,),
    ).fetchall()
    trial["events"] = [
        {**dict(e), "fault_injected": bool(e["fault_injected"]), "payload": _load(e["payload"]),
         "channel": e["channel"] or "agent"}
        for e in events
    ]
    return trial


def _load(payload: str | None):
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except ValueError:  # truncated on write
        return {"truncated": payload}
