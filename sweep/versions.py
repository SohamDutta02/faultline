"""Which harness versions produced a sweep's results, and which are stale.

A harness rots like code: prompts get edited, retry policies tuned, checks
extended. Every trial records the fingerprint of the harness that produced
it, so a sweep can tell when its results no longer describe the harness its
architecture ids name today.
"""

from agents.registry import BadHarness, resolve
from harness import SLOTS, changed_parts

from . import store

PARTS = ("policy", *SLOTS)


def current_fingerprint(arch_id: str) -> str | None:
    try:
        return resolve(arch_id).fingerprint
    except BadHarness:
        return None


def staleness(conn, sweep_id: str, archs: list[str] | None = None) -> list[dict]:
    """Per architecture: the current fingerprint, the recorded ones, how many trials
    came from another version (stale) or from before fingerprints (unknown), and
    which parts of the harness changed since."""
    versions = store.harness_versions(conn, sweep_id)
    out = []
    for arch_id in archs or sorted(versions):
        recorded = versions.get(arch_id)
        if not recorded:
            continue
        current = current_fingerprint(arch_id)
        stale_fps = [fp for fp in recorded if fp is not None and fp != current]
        changed = set()
        if current is None:
            changed.add("unresolvable")
        else:
            now = resolve(arch_id).manifest()
            for fp in stale_fps:
                old = store.get_harness(conn, fp)
                if old:
                    changed |= set(changed_parts(old["manifest"], now))
        out.append({
            "arch": arch_id,
            "current": current,
            "recorded": {fp or "unknown": n for fp, n in recorded.items()},
            "versions": len([fp for fp in recorded if fp is not None]),
            "stale": sum(recorded[fp] for fp in stale_fps),
            "unknown": recorded.get(None, 0),
            "changed": [p for p in (*PARTS, "unresolvable") if p in changed],
        })
    return out


def describe(s: dict) -> str | None:
    """One line about an architecture's versions, or None if its results are current."""
    notes = []
    if s["stale"]:
        what = ", ".join(s["changed"]) or "something"
        notes.append(f"{s['stale']} trials come from an older harness ({what} changed since)")
    if s["versions"] > 1:
        notes.append(f"results mix {s['versions']} harness versions")
    if s["unknown"]:
        notes.append(f"{s['unknown']} trials predate harness fingerprints, so their version is unknown")
    return f"{s['arch']}: " + "; ".join(notes) if notes else None
