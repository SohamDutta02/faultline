"""Tools: the tool interface the model sees and the client that executes calls."""

from agents.toolbox import ResilientToolbox, Toolbox, llm_tools

from .base import Option


def _content(client: type[Toolbox], policy: dict | None) -> dict:
    return {"client": client.__name__, "policy": policy, "tools": llm_tools()}


RETRY_POLICY = {
    "max_attempts": ResilientToolbox.max_attempts,
    "base_backoff_s": ResilientToolbox.base_backoff_s,
    "max_backoff_s": ResilientToolbox.max_backoff_s,
    "reads": "retried on transport errors, 5xx and unparseable bodies",
    "writes": "retried only on 429",
}

OPTIONS = {
    o.id: o
    for o in [
        Option("plain", "calls each tool once and hands every error to the model", _content(Toolbox, None),
               impl=Toolbox),
        Option("retrying", "client-side retries with backoff, the way a production SDK adds them",
               _content(ResilientToolbox, RETRY_POLICY), impl=ResilientToolbox),
    ]
}
