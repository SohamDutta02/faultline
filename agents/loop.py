import json
from collections.abc import Callable
from dataclasses import dataclass, field

from .llm import Backend, Reply
from .toolbox import Toolbox


class StepBudgetExceeded(Exception):
    pass


@dataclass
class AgentContext:
    """Everything an architecture gets for one trial. Usage is metered here so
    the runner can report it even when the run ends in an exception."""

    toolbox: Toolbox
    backend: Backend | None
    max_steps: int = 12
    tokens: int = 0
    llm_calls: int = 0
    # name -> factory for the extra tool clients feedback can read through
    channels: dict[str, Callable[[], Toolbox]] = field(default_factory=dict)
    # grades the world at a checkpoint: answer -> {"outcome", "reason"}
    on_checkpoint: Callable[[str | None], dict] | None = None
    checkpoints: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    # per-trial scratch space for harness components
    memory: dict = field(default_factory=dict)
    _open: dict[str, Toolbox] = field(default_factory=dict)

    def channel(self, name: str) -> Toolbox:
        """The tool client for a channel, opened on first use. Without a factory,
        "review" falls back to the agent's own client."""
        if name == "agent":
            return self.toolbox
        if name not in self._open:
            factory = self.channels.get(name)
            if factory is None:
                if name == "review":
                    return self.toolbox
                raise ValueError(f"no {name} channel in this context")
            self._open[name] = factory()
        return self._open[name]

    @property
    def toolboxes(self) -> list[Toolbox]:
        return [self.toolbox, *self._open.values()]

    def usage(self) -> dict:
        tbs = self.toolboxes
        return {
            "tool_calls": sum(t.call_count for t in tbs),
            "tool_errors": sum(t.error_count for t in tbs),
            "retries": sum(t.retries for t in tbs),
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
        }

    def checkpoint(self, label: str, answer: str | None):
        """Record where the trial stands, e.g. the agent's first answer before
        feedback, graded against the world if the runner can grade it."""
        record = {"label": label, **self.usage()}
        if self.on_checkpoint is not None:
            record.update(self.on_checkpoint(answer))
        self.checkpoints.append(record)
        self.toolbox.log("checkpoint", payload=record)

    def complete(self, messages: list[dict], tools: list[dict], system: str, role: str = "agent") -> Reply:
        reply = self.backend.complete(messages, tools, system)
        self.llm_calls += 1
        self.tokens += reply.tokens
        self.toolbox.log(
            "llm",
            payload={
                "role": role,
                "text": reply.text[:1000],
                "calls": [{"name": c.name, "args": c.args} for c in reply.calls],
                "tokens": reply.tokens,
                "finish": reply.finish,
            },
        )
        return reply


class Conversation:
    """One model's view of the task: a system prompt, a tool set, a history.

    state, if given, is a harness state tracker (harness/state.py): it observes
    every tool call and its rendering is appended to the system prompt before
    each model call."""

    def __init__(self, ctx: AgentContext, system: str, tool_names: list[str] | None = None, role: str = "agent",
                 state=None, toolbox: Toolbox | None = None):
        self.ctx = ctx
        self.system = system
        self.role = role
        self.tool_names = tool_names
        self.toolbox = toolbox or ctx.toolbox
        self.tools = self.toolbox.llm_tools(tool_names)
        self.messages: list[dict] = []
        self.state = state
        self._shown = ""

    def _system(self) -> str:
        if self.state is None:
            return self.system
        rendered = self.state.render()
        if rendered != self._shown:
            self._shown = rendered
            self.ctx.toolbox.log("state", payload={"role": self.role, "text": rendered})
        return f"{self.system}\n\n{rendered}" if rendered else self.system

    def say(self, text: str, max_steps: int) -> str | None:
        """Send a user message, then execute tool calls until the model answers in text."""
        self.messages.append({"role": "user", "text": text})
        for _ in range(max_steps):
            reply = self.ctx.complete(self.messages, self.tools, self._system(), self.role)
            if not reply.calls:
                if reply.text:
                    self.messages.append({"role": "assistant", "reply": reply})
                return reply.text or None

            self.messages.append({"role": "assistant", "reply": reply})
            results = []
            for call in reply.calls:
                body, is_error = self.toolbox.call(call.name, call.args, allowed=self.tool_names)
                if self.state is not None:
                    self.state.observe(call.name, call.args, body, self.toolbox.last_status)
                results.append({"call": call, "body": body, "is_error": is_error})
            self.messages.append({"role": "tool", "results": results})
        raise StepBudgetExceeded()

    def transcript(self, max_body: int = 400) -> str:
        lines = []
        for m in self.messages:
            if m["role"] == "tool":
                for r in m["results"]:
                    args = json.dumps(r["call"].args, sort_keys=True)
                    status = "ERROR" if r["is_error"] else "ok"
                    lines.append(f"- {r['call'].name}({args}) -> [{status}] {r['body'][:max_body]}")
        return "\n".join(lines) or "(no tool calls)"
