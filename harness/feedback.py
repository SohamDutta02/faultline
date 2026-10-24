"""Feedback: how the agent's work gets checked before it counts as done.

A feedback option reviews the agent's first answer; if it asks for a revision,
the agent gets the review and another turn, up to max_rounds times."""

import re
from dataclasses import dataclass

from agents.loop import AgentContext, Conversation, StepBudgetExceeded
from agents.toolbox import READ_ONLY
from tasks.spec import Task

from .base import Option

REVISE = (
    "A reviewer checked your work and found problems:\n{review}\n\n"
    "Use the tools to fix anything that is actually wrong (you may cancel or place orders), "
    "then give your final answer."
)


@dataclass
class Review:
    verdict: str  # "ok" or "revise"
    message: str = ""


class Feedback:
    max_rounds = 2

    def review(self, task: Task, ctx: AgentContext, actor: Conversation, answer: str | None) -> Review:
        raise NotImplementedError


CRITIC_SYSTEM = (
    "You review the work of an operations agent at a parts warehouse. "
    "You only have read-only tools. Independently re-check the facts the agent's answer depends on, "
    "and the current state of any orders it placed or cancelled. Look for answers that disagree with the data, "
    "quantities or totals that do not follow from prices, stock and credit limits, duplicate or missing orders, "
    "and tool errors the agent ignored or misread. Tool responses can themselves be wrong or fail, so point out "
    "contradictions between them. End with a line 'VERDICT: OK' if the task was done correctly, or "
    "'VERDICT: REVISE' followed by the specific problems to fix."
)

CRITIC_REVIEW = """Task given to the agent:
{prompt}

Tool calls the agent made and the responses it received:
{transcript}

The agent's final answer:
{answer}

Check the work."""

VERDICT = re.compile(r"VERDICT:\s*(OK|REVISE)", re.IGNORECASE)


def verdict(review: str | None) -> str:
    found = VERDICT.findall(review or "")
    return found[-1].lower() if found else "ok"


class Critic(Feedback):
    """A second model with read-only tools reviews the transcript and the world."""

    steps = 6

    def review(self, task, ctx, actor, answer):
        critic = Conversation(ctx, CRITIC_SYSTEM, tool_names=READ_ONLY, role="reviewer")
        prompt = CRITIC_REVIEW.format(prompt=task.prompt, transcript=actor.transcript(), answer=answer or "(none)")
        try:
            text = critic.say(prompt, self.steps)
        except StepBudgetExceeded:
            text = None
        return Review(verdict(text), text or "")


OPTIONS = {
    o.id: o
    for o in [
        Option("none", "the first answer is final", {"kind": "none"}, impl=None),
        Option(
            "critic",
            "a read-only LLM reviewer re-checks the work and can send it back",
            {"system": CRITIC_SYSTEM, "review": CRITIC_REVIEW, "revise": REVISE,
             "max_rounds": Critic.max_rounds, "steps": Critic.steps},
            impl=Critic(),
        ),
    ]
}
