import json
from dataclasses import dataclass

from .llm import Backend, Reply
from .toolbox import Toolbox, llm_tools


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
    """One model's view of the task: a system prompt, a tool set, a history."""

    def __init__(self, ctx: AgentContext, system: str, tool_names: list[str] | None = None, role: str = "agent"):
        self.ctx = ctx
        self.system = system
        self.role = role
        self.tool_names = tool_names
        self.tools = llm_tools(tool_names)
        self.messages: list[dict] = []

    def say(self, text: str, max_steps: int) -> str | None:
        """Send a user message, then execute tool calls until the model answers in text."""
        self.messages.append({"role": "user", "text": text})
        for _ in range(max_steps):
            reply = self.ctx.complete(self.messages, self.tools, self.system, self.role)
            if not reply.calls:
                if reply.text:
                    self.messages.append({"role": "assistant", "reply": reply})
                return reply.text or None

            self.messages.append({"role": "assistant", "reply": reply})
            results = []
            for call in reply.calls:
                body, is_error = self.ctx.toolbox.call(call.name, call.args, allowed=self.tool_names)
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
