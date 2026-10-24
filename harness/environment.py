"""Environment: the world the tools act on, and the guarantees it offers.

The fault configs are what the experiment does to the environment; this slot
is what the harness designer chooses about it."""

from agents.toolbox import IDEMPOTENT_NOTES

from .base import Option

OPTIONS = {
    o.id: o
    for o in [
        Option("bare", "the warehouse as it is: writes are not idempotent", {"idempotent_writes": False},
               impl={"idempotent": False}),
        Option(
            "idempotent",
            "writes carry an idempotency key, so a repeated write returns the first result instead of acting twice",
            {
                "idempotent_writes": True,
                "key": "hash of the trial's nonce, the tool and its arguments",
                "retrying_clients": "retry writes like reads, since a repeat can't act twice",
                "tool_notes": IDEMPOTENT_NOTES,
            },
            impl={"idempotent": True},
        ),
    ]
}
