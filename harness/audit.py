"""Audit the harness for rot: python -m harness.audit [--db PATH] [--calibrate] [--strict]

A harness drifts away from the system it wraps and from the results that
describe it. These checks look for the drift that's cheap to detect:

  static (always)
    tool_spec_drift        the tool specs the model sees vs the toolserver's real routes
    prompt_tool_refs       prompts and notes that name tools that don't exist
    presets                every preset and control resolves; every option is described
    dead_options           options no preset uses (info)
  against recorded results (--db, or the default database if it exists)
    unbumped_code          an option's code changed but its version didn't, so old
                           results carry a fingerprint that looks current
    stale_results          the latest sweep with a preset was run on an older harness
  calibration (--calibrate; starts a local stack)
    verification           the checks raise no false alarms on correct work and catch
                           seeded failures: verify the verifier

Exit status 1 on any error (with --strict, on any warning too).
"""

import argparse
import json
import re
from dataclasses import asdict, dataclass

from agents.registry import ARCHITECTURES, SCRIPTED, BadHarness, resolve
from agents.toolbox import BY_NAME, SPECS

from .base import SLOTS
from .components import OPTIONS
from .spec import PRESETS, changed_parts

# parameters the harness adds to tool calls itself; the model never sees them
HARNESS_PARAMS = {"request_id"}
TOOLISH = re.compile(r"\b(?:search|get|list|place|cancel|create|update|delete|fetch)_[a-z_]+\b")
LEVELS = ("error", "warning", "info")


@dataclass
class Finding:
    level: str
    check: str
    message: str


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def check_tool_specs(specs=None, openapi=None) -> list[Finding]:
    if openapi is None:
        from toolserver.app import app
        openapi = app.openapi()
    specs = SPECS if specs is None else specs
    routes = {}
    for path, ops in openapi["paths"].items():
        if path.startswith("/tools/"):
            for method, op in ops.items():
                params = {p["name"]: bool(p.get("required")) for p in op.get("parameters", []) if p["in"] == "query"}
                routes[path] = (method.upper(), params)
    out = []
    for s in specs:
        route = routes.pop(s["path"], None)
        if route is None:
            out.append(Finding("error", "tool_spec_drift", f"{s['name']}: the toolserver has no route {s['path']}"))
            continue
        method, params = route
        if method != s["method"]:
            out.append(Finding("error", "tool_spec_drift", f"{s['name']}: spec says {s['method']}, route is {method}"))
        schema = s["input_schema"]
        declared = set(schema.get("properties", {}))
        required = set(schema.get("required", []))
        actual = {k for k in params if k not in HARNESS_PARAMS}
        actual_required = {k for k, req in params.items() if req and k not in HARNESS_PARAMS}
        if declared != actual:
            out.append(Finding("error", "tool_spec_drift", f"{s['name']}: the model is offered "
                               f"{sorted(declared)}, the route takes {sorted(actual)}"))
        elif required != actual_required:
            out.append(Finding("error", "tool_spec_drift",
                               f"{s['name']}: required {sorted(required)} in the spec, {sorted(actual_required)} "
                               "on the route"))
    for path in routes:
        out.append(Finding("error", "tool_spec_drift", f"the toolserver serves {path} but no tool spec offers it"))
    return out


def check_prompt_refs(options=None) -> list[Finding]:
    out = []
    for slot, opts in (options or OPTIONS).items():
        for opt in opts.values():
            for text in _strings(opt.content):
                for name in sorted(set(TOOLISH.findall(text)) - set(BY_NAME)):
                    out.append(Finding("error", "prompt_tool_refs",
                                       f"{slot}={opt.id} mentions {name}, which is not a tool"))
    return out


def check_presets() -> list[Finding]:
    out = []
    for name in [*PRESETS, *SCRIPTED]:
        try:
            resolve(name)
        except BadHarness as exc:
            out.append(Finding("error", "presets", f"{name} doesn't resolve: {exc}"))
    for slot, opts in OPTIONS.items():
        for opt in opts.values():
            if not opt.description:
                out.append(Finding("error", "presets", f"{slot}={opt.id} has no description"))
    used = {(slot, getattr(a.spec, slot)) for a in ARCHITECTURES.values() for slot in SLOTS}
    for slot, opts in OPTIONS.items():
        for opt in opts.values():
            if (slot, opt.id) not in used:
                out.append(Finding("info", "dead_options", f"{slot}={opt.id} is in no preset; it runs only "
                                   f"when named, e.g. single+{slot}={opt.id}"))
    return out


def check_recorded(conn) -> list[Finding]:
    from sweep import versions

    out = []
    seen = set()
    for row in conn.execute("select * from harness order by first_seen"):
        try:
            arch = resolve(row["arch_id"])
        except BadHarness:
            continue
        old_manifest, old_code = json.loads(row["manifest"]), json.loads(row["code"] or "{}")
        now_manifest, now_code = arch.manifest(), arch.code()
        same = set(arch.slots + ("policy",)) - set(changed_parts(old_manifest, now_manifest))
        for part in sorted(same):
            if old_code.get(part) and now_code.get(part) and old_code[part] != now_code[part]:
                entry = now_manifest[part]
                key = (part, entry.get("id"), old_code[part])
                if key in seen:
                    continue
                seen.add(key)
                out.append(Finding(
                    "warning", "unbumped_code",
                    f"{part}={entry.get('id')}: its code changed since {row['first_seen'][:10]} but its version is "
                    f"still {entry.get('version')}, so results recorded under the old code look current. "
                    "Bump the version (or revert the change)."))

    latest: dict[str, tuple] = {}
    for sweep in conn.execute("select * from sweep where label != 'playground' order by started_at"):
        for s in versions.staleness(conn, sweep["id"]):
            if s["arch"] in ARCHITECTURES:
                latest[s["arch"]] = (sweep["label"], s)
    for arch_id, (label, s) in sorted(latest.items()):
        if s["stale"]:
            out.append(Finding("warning", "stale_results",
                               f"{arch_id}: the latest results (sweep {label}) come from an older harness "
                               f"({', '.join(s['changed']) or 'something'} changed). Re-run it."))
    return out


def check_calibration() -> list[Finding]:
    """Run the oracle on every task, then the checks over its trace: correct work
    must pass through both channels, and seeded failures must be caught."""
    import httpx

    from agents.toolbox import Toolbox
    from runner.stack import local_stack
    from runner.trial import run_trial
    from tasks.catalog import BY_ID, CATALOG

    from .checks import agent_calls, reader, run_checks

    out = []
    with local_stack() as (ts, px):
        def set_faults(faults):
            httpx.post(f"{px}/_proxy/config", params={"name": "audit", "seed": 0},
                       json={"faults": faults}).raise_for_status()

        def problems(task, arch="oracle", via=ts):
            r = run_trial(task, arch, toolserver_url=ts, proxy_url=px)
            tb = Toolbox(via)
            try:
                return r, run_checks(agent_calls(r.events), reader(tb), r.answer)
            finally:
                tb.close()

        alarms = 0
        for task in CATALOG:
            for via in (ts, px):
                set_faults([])
                r, found = problems(task, via=via)
                if r.outcome == "pass" and found:
                    alarms += 1
                    out.append(Finding("error", "verification",
                                       f"false alarm on correct work ({task.id}, via "
                                       f"{'truth' if via == ts else 'proxy'}): {found[0].text}"))
        seeded = [
            ("a write that errored but went through", "t2_simple_order", "oracle",
             [{"type": "partial_write", "target": "place_order", "max_fires": 1}]),
            ("a write that never happened", "t2_simple_order", "oracle",
             [{"type": "timeout", "target": "place_order", "max_fires": 1, "params": {"delay_s": 0}}]),
            ("a stale read", "t1_stock_lookup", "oracle", [{"type": "stale_data", "target": "search_inventory"}]),
            ("an altered price", "t5_tier_quote", "oracle", [{"type": "plausible_wrong", "target": "get_pricing"}]),
        ]
        caught = 0
        for what, task_id, arch, faults in seeded:
            set_faults(faults)
            _, found = problems(BY_ID[task_id], arch)
            if found:
                caught += 1
            else:
                out.append(Finding("error", "verification", f"the checks miss {what} ({task_id})"))
        set_faults([])
    out.append(Finding("info", "verification",
                       f"{alarms} false alarms on {2 * len(CATALOG)} correct runs; {caught}/{len(seeded)} seeded "
                       "failures caught"))
    return out


def run(conn=None, calibrate: bool = False) -> list[Finding]:
    findings = check_tool_specs() + check_prompt_refs() + check_presets()
    if conn is not None:
        findings += check_recorded(conn)
    if calibrate:
        findings += check_calibration()
    return sorted(findings, key=lambda f: (LEVELS.index(f.level), f.check))


def main(argv=None):
    from pathlib import Path

    from sweep import store

    ap = argparse.ArgumentParser(description="audit the harness for drift and stale results")
    ap.add_argument("--db", default=None,
                    help=f"results database to check against (default {store.DB_PATH}, if it exists)")
    ap.add_argument("--calibrate", action="store_true", help="also verify the verification checks on a local stack")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    db = args.db or (store.DB_PATH if Path(store.DB_PATH).exists() else None)
    conn = store.connect(db) if db else None
    findings = run(conn, args.calibrate)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.level:8} {f.check:18} {f.message}")
        counts = {lvl: sum(f.level == lvl for f in findings) for lvl in LEVELS}
        print(f"\n{counts['error']} errors, {counts['warning']} warnings, {counts['info']} notes"
              + ("" if db else " (no results database checked)"))
    bad = {"error", "warning"} if args.strict else {"error"}
    raise SystemExit(1 if any(f.level in bad for f in findings) else 0)


if __name__ == "__main__":
    main()
