from collections.abc import Callable
from dataclasses import dataclass

from tasks.spec import Task

from . import critic, oracle, single
from .loop import AgentContext
from .toolbox import ResilientToolbox, Toolbox


@dataclass(frozen=True)
class Architecture:
    id: str
    run: Callable[[Task, AgentContext], str | None]
    toolbox: type[Toolbox] = Toolbox
    needs_llm: bool = True
    description: str = ""


ARCHITECTURES = {
    a.id: a
    for a in [
        Architecture("single", single.run, Toolbox, True, "one tool-calling loop, no safety nets"),
        Architecture("retry", single.run, ResilientToolbox, True, "single + client-side retries for transport and schema faults"),
        Architecture("critic", critic.run, Toolbox, True, "single + read-only reviewer that can send the work back"),
        Architecture("oracle", oracle.run, Toolbox, False, "scripted per-task policy, no LLM (control)"),
    ]
}
