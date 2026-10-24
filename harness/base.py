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

# bump when the agent loop or the harness runner changes behaviour (they sit
# under every LLM harness, so they are fingerprinted as its "policy")
LLM_POLICY_VERSION = 1

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
    code lists the classes, functions or modules that implement the option;
    their source is hashed separately so the audit can catch code that
    changed without a version bump.
    """

    id: str
    description: str
    content: dict = field(default_factory=dict, compare=False)
    version: int = 1
    impl: Any = field(default=None, compare=False, repr=False)
    code: tuple = field(default=(), compare=False, repr=False)


def code_hash(objects) -> str | None:
    """A hash of the source of the given classes/functions/modules; None if there are none."""
    import hashlib
    import inspect

    if not objects:
        return None
    h = hashlib.sha256()
    for obj in objects:
        h.update(inspect.getsource(obj).encode())
    return h.hexdigest()[:12]
