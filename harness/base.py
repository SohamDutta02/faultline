"""The parts every harness subsystem shares.

A harness is everything around the model that decides how much of its
capability gets realized. faultline splits it into five subsystems, each a
slot with named options:

  instructions  what the model is told
  tools         the tool interface and the client that calls it
  environment   the world the tools act on, and the guarantees it gives
  state         what the harness keeps track of between steps
  feedback      how work gets checked before it counts as done
"""

from dataclasses import dataclass, field
from typing import Any

SLOTS = ("instructions", "tools", "environment", "state", "feedback")

SLOT_DESCRIPTIONS = {
    "instructions": "what the model is told: the system prompt",
    "tools": "the tool interface and the client that calls it",
    "environment": "the world the tools act on, and the guarantees it gives",
    "state": "what the harness keeps track of between steps",
    "feedback": "how work gets checked before it counts as done",
}


@dataclass(frozen=True)
class Option:
    """One way to fill a slot.

    content is what the harness fingerprint hashes: prompts, tool schemas,
    policy parameters. Behaviour that lives in code is covered by version, so
    bump it when you change what an option does without changing its content.
    """

    id: str
    description: str
    content: dict = field(default_factory=dict, compare=False)
    version: int = 1
    impl: Any = field(default=None, compare=False, repr=False)
