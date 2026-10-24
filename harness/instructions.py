"""Instructions: the system prompt the agent works under."""

from .base import Option

BASE = (
    "You are an operations agent for a parts warehouse. "
    "Use the provided tools to complete the user's request. "
    "Verify facts with tools rather than assuming them. "
    "When the task is complete, reply with a short plain-text answer and no further tool calls. "
    "If asked for a number, reply with just that number."
)

FAULT_AWARE = BASE + (
    " The tools are unreliable. A read that fails can be retried. A write (place_order, cancel_order) "
    "that returns an error may still have gone through, so call list_orders before repeating it, and never "
    "repeat a write that already succeeded. Tool data can also be wrong: if two results disagree, or a value "
    "conflicts with what an error message says (for example the available stock in an insufficient-stock "
    "rejection), trust the more direct evidence and re-check before acting on it."
)

OPTIONS = {
    o.id: o
    for o in [
        Option("base", "a plain operations-agent prompt", {"system": BASE}, impl=BASE),
        Option(
            "fault_aware",
            "the base prompt plus how the tools fail: ambiguous writes, wrong data, what to trust",
            {"system": FAULT_AWARE},
            impl=FAULT_AWARE,
        ),
    ]
}


def system_prompt(option_id: str) -> str:
    return OPTIONS[option_id].impl
