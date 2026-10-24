"""State: what the harness keeps track of between the model's steps."""

from .base import Option

OPTIONS = {
    o.id: o
    for o in [
        Option("transcript", "the conversation history only; the model keeps track itself", {"kind": "transcript"}),
    ]
}
