"""Environment: the world the tools act on, and the guarantees it offers.

The fault configs are what the experiment does to the environment; this slot
is what the harness designer chooses about it."""

from .base import Option

OPTIONS = {
    o.id: o
    for o in [
        Option("bare", "the warehouse as it is: writes are not idempotent", {"idempotent_writes": False},
               impl={"idempotent": False}),
    ]
}
