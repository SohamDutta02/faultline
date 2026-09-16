from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Outcome(str, Enum):
    PASS = "pass"
    FAIL_WRONG_ANSWER = "fail_wrong_answer"
    FAIL_NO_ANSWER = "fail_no_answer"
    FAIL_LOOP = "fail_loop"
    FAIL_CRASH = "fail_crash"
    FAIL_HARNESS = "fail_harness"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Verdict:
    outcome: Outcome
    checks: list[CheckResult] = field(default_factory=list)
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS


@dataclass
class Task:
    id: str
    prompt: str
    category: str
    seed: int
    min_tool_calls: int
    checks: list[Callable[[dict, dict, Any], CheckResult]]


def check(name: str):
    def wrap(fn):
        def inner(before: dict, after: dict, answer: Any) -> CheckResult:
            try:
                ok, detail = fn(before, after, answer)
            except Exception as exc:
                return CheckResult(name, False, f"check raised: {exc!r}")
            return CheckResult(name, ok, detail)
        inner.check_name = name
        return inner
    return wrap