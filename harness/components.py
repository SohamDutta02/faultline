"""Every option of every slot, keyed by slot then option id."""

from . import environment, feedback, instructions, state, tools
from .base import SLOTS, Option

OPTIONS: dict[str, dict[str, Option]] = {
    "instructions": instructions.OPTIONS,
    "tools": tools.OPTIONS,
    "environment": environment.OPTIONS,
    "state": state.OPTIONS,
    "feedback": feedback.OPTIONS,
}
assert tuple(OPTIONS) == SLOTS


def option(slot: str, option_id: str) -> Option:
    return OPTIONS[slot][option_id]
