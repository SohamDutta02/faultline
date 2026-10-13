"""Sanity-check the task catalog: the scripted oracle must pass every task with no faults.

Run from the repo root: python -m scripts.oracle_check
"""

import httpx

from runner.stack import local_stack
from runner.trial import run_trial
from tasks.catalog import CATALOG


def main():
    failed = 0
    with local_stack() as (toolserver_url, proxy_url):
        httpx.post(f"{proxy_url}/_proxy/config", params={"name": "none"}).raise_for_status()
        for task in CATALOG:
            r = run_trial(task, "oracle", toolserver_url=toolserver_url, proxy_url=proxy_url)
            failed += r.outcome != "pass"
            print(f"{task.id}: {r.outcome} {r.reason}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
