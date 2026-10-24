import argparse
import json

from . import store


def list_trials(conn, label: str, filters: dict, limit: int):
    sweep = store.find_sweep(conn, label)
    if not sweep:
        print(f"no sweep labelled {label}")
        return
    where, params = ["sweep_id = ?"], [sweep["id"]]
    for col, val in filters.items():
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    rows = conn.execute(
        f"""select * from trial where {' and '.join(where)}
            order by fault_config, task_id, architecture_id, repeat_idx limit ?""",
        params + [limit],
    ).fetchall()
    for r in rows:
        flag = "F" if r["fault_fired"] else " "
        print(
            f"{r['id'][:8]}  {r['fault_config']:16} {r['task_id']:18} {r['architecture_id']:12} "
            f"r{r['repeat_idx']} {flag} {r['outcome']:18} {(r['reason'] or '')[:70]}"
        )
    print(f"\n{len(rows)} trials. Show one with --trial <id prefix>")


def _summary(kind: str, payload: dict) -> str:
    if kind == "tool_call":
        return json.dumps(payload, sort_keys=True)
    if kind == "tool_result":
        return f"[{payload.get('status')}] {payload.get('body', '')[:160]}"
    if kind == "llm":
        calls = ", ".join(f"{c['name']}({json.dumps(c['args'], sort_keys=True)})" for c in payload.get("calls", []))
        text = payload.get("text", "").replace("\n", " ")[:160]
        return f"{payload.get('tokens', 0)} tok  " + (f"calls {calls}" if calls else f"says {text!r}")
    if kind in ("critic", "feedback"):
        source = payload.get("source", "critic")
        return f"{source} round {payload.get('round')}: {payload.get('verdict')}  {payload.get('review', '')[:160]!r}"
    if kind == "checkpoint":
        return f"{payload.get('label')}: {payload.get('outcome', '?')}  {payload.get('reason', '')[:140]}"
    if kind == "state":
        return payload.get("text", "").replace("\n", " | ")[:200]
    if kind == "crash":
        return payload.get("traceback", "")[-300:]
    return json.dumps(payload)[:200]


def show_trial(conn, prefix: str):
    rows = conn.execute("select * from trial where id like ?", (prefix + "%",)).fetchall()
    if len(rows) != 1:
        print(f"{len(rows)} trials match {prefix!r}")
        return
    t = rows[0]
    print(f"trial {t['id']}")
    print(f"  {t['task_id']} / {t['architecture_id']} / {t['fault_config']} / repeat {t['repeat_idx']} (seed {t['seed']})")
    print(f"  outcome {t['outcome']}: {t['reason']}")
    print(f"  answer  {t['answer']!r}")
    print(f"  tool calls {t['tool_calls']}, tool errors {t['tool_errors']}, retries {t['retries']}, "
          f"llm calls {t['llm_calls']}, tokens {t['tokens_used']}")
    print()
    events = conn.execute(
        "select * from trial_event where trial_id = ? order by step_idx", (t["id"],)
    ).fetchall()
    for e in events:
        flag = "F" if e["fault_injected"] else " "
        payload = json.loads(e["payload"]) if e["payload"] else {}
        channel = e["channel"] if "channel" in e.keys() and e["channel"] and e["channel"] != "agent" else ""
        print(f"{e['step_idx']:>3} {flag} {e['kind']:11} {channel:6} {e['tool_name'] or '':16} "
              f"{_summary(e['kind'], payload)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="list a sweep's trials or replay one trial's events")
    ap.add_argument("--label")
    ap.add_argument("--trial", help="trial id or unique prefix")
    ap.add_argument("--config", dest="fault_config")
    ap.add_argument("--arch", dest="architecture_id")
    ap.add_argument("--task", dest="task_id")
    ap.add_argument("--outcome")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    conn = store.connect(args.db)
    if args.trial:
        show_trial(conn, args.trial)
    elif args.label:
        filters = {k: getattr(args, k) for k in ("fault_config", "architecture_id", "task_id", "outcome")}
        list_trials(conn, args.label, filters, args.limit)
    else:
        ap.error("pass --label to list trials or --trial to show one")


if __name__ == "__main__":
    main()
