"""Regrade a sweep's answered trials with the current report grading.

Trials recorded before reports were graded have no report, and one whose world
was right but whose answer said the work failed is stored as a pass. The world
isn't stored, but its verdict is: the task's checks passed if the trial passed
(or was a misreport) and failed if it was fail_wrong_answer. With the stored
answer, that is all tasks.verify.judge needs, so no trial is re-run.

A feedback trial's first answer is regraded too, from the answer its
checkpoint recorded or, for trials recorded before checkpoints kept it, the
actor's last reply before the checkpoint. Attributions are recomputed after.
"""

import argparse
import json

from tasks.catalog import BY_ID
from tasks.verify import judge

from . import attribution, store

ANSWERED = ("pass", "fail_wrong_answer", "fail_misreport")


def regrade_answer(task_id: str, outcome: str, reason: str, answer) -> tuple[str, str, str] | None:
    """(outcome, reason, report) under the current grading, or None for a trial
    without an answer to grade (no answer, loop, crash, harness failure)."""
    task = BY_ID.get(task_id)
    if task is None or outcome not in ANSWERED or answer is None:
        return None
    checks_pass = outcome != "fail_wrong_answer"
    new_outcome, new_reason, report = judge(task, str(answer), checks_pass)
    # the check failures of a wrong world are the reason; judge doesn't know them
    return new_outcome.value, new_reason if checks_pass else reason, report.value


def first_answer(fb: dict, events: list[dict], final_answer):
    """The answer a feedback trial's first-pass checkpoint graded."""
    first = fb.get("first_pass") or {}
    if "answer" in first:
        return first["answer"]
    reviews = fb.get("reviews") or []
    if not reviews or reviews[0].get("verdict") != "revise":
        return final_answer  # nothing asked for a revision, so the first answer is the final one
    last = None
    for e in events:
        p = e.get("payload") or {}
        if e.get("kind") == "llm" and p.get("role", "agent") == "agent" and not p.get("calls"):
            last = p.get("text")
        elif e.get("kind") == "checkpoint":
            return last
    return None


def regrade_trial(conn, trial_id: str) -> bool:
    """Regrade one stored trial in place, and recompute its attribution; True if its
    grading (outcome, reason, report, or its first answer's) changed."""
    t = store.get_trial(conn, trial_id)
    changed = False
    new = regrade_answer(t["task_id"], t["outcome"], t["reason"] or "", t["answer"])
    if new and new != (t["outcome"], t["reason"] or "", t["report"]):
        t["outcome"], t["reason"], t["report"] = new
        changed = True

    fb = t["feedback"]
    if fb and fb.get("first_pass"):
        first = fb["first_pass"]
        answer = first_answer(fb, t["events"], t["answer"])
        regraded = regrade_answer(t["task_id"], first.get("outcome"), first.get("reason") or "", answer)
        if regraded and regraded != (first.get("outcome"), first.get("reason") or "", first.get("report")):
            first["outcome"], first["reason"], first["report"] = regraded
            changed = True

    a = attribution.attribute(t)
    if changed or a != t["attribution"]:
        conn.execute(
            "update trial set outcome = ?, reason = ?, report = ?, feedback = ?, attribution = ? where id = ?",
            (t["outcome"], t["reason"], t["report"], json.dumps(fb) if fb else None,
             json.dumps(a) if a else None, trial_id),
        )
    return changed


def regrade(conn, label: str) -> dict:
    """Regrade every trial in a sweep: {"trials", "changed", "misreports"}."""
    sweep = store.find_sweep(conn, label)
    if not sweep:
        raise SystemExit(f"no sweep labelled {label}")
    ids = [r["id"] for r in conn.execute("select id from trial where sweep_id = ?", (sweep["id"],))]
    changed = sum(regrade_trial(conn, tid) for tid in ids)
    conn.commit()
    misreports = conn.execute("select count(*) from trial where sweep_id = ? and outcome = 'fail_misreport'",
                              (sweep["id"],)).fetchone()[0]
    return {"trials": len(ids), "changed": changed, "misreports": misreports}


def main(argv=None):
    ap = argparse.ArgumentParser(description="regrade a sweep's answers with the current report grading")
    ap.add_argument("--label", required=True)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    r = regrade(store.connect(args.db), args.label)
    print(f"regraded {args.label}: {r['changed']} of {r['trials']} trials changed; {r['misreports']} misreports")


if __name__ == "__main__":
    main()
