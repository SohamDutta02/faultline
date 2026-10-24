"""Feedback: how the agent's work gets checked before it counts as done.

A feedback option reviews the agent's first answer; if it asks for a revision,
the agent gets the review and another turn, up to max_rounds times.

Each option reads through a channel. "review" goes through the same fault
proxy as the agent, so a fault that fools the agent can fool the check too.
"truth" reads the toolserver directly: an independent source of truth, which
real agents don't always have. Treat the *_independent options as an upper
bound on what feedback can do, not as a deployable design.
"""

import re
from dataclasses import dataclass

from agents.loop import AgentContext, Conversation, StepBudgetExceeded
from agents.toolbox import READ_ONLY
from tasks.spec import Task

from . import checks as checks_module
from .base import Option
from .checks import agent_calls, reader, run_checks

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

    def __init__(self, channel: str = "review"):
        self.channel = channel

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
        critic = Conversation(ctx, CRITIC_SYSTEM, tool_names=READ_ONLY, role="reviewer",
                              toolbox=ctx.channel(self.channel))
        prompt = CRITIC_REVIEW.format(prompt=task.prompt, transcript=actor.transcript(), answer=answer or "(none)")
        try:
            text = critic.say(prompt, self.steps)
        except StepBudgetExceeded:
            text = None
        return Review(verdict(text), text or "")


class Checks(Feedback):
    """Deterministic verification (harness/checks.py): no model, a few reads.

    A wrong fact is reported once: the agent can't un-read it, only stop
    relying on it, so repeating it in the next round would just loop. Problems
    with the world (orders) are reported for as long as they last."""

    def review(self, task, ctx, actor, answer):
        found = run_checks(agent_calls(ctx.toolbox.events), reader(ctx.channel(self.channel)), answer)
        told = ctx.memory.setdefault("checks_reported_facts", set())
        problems = [p for p in found if not (p.kind == "fact" and p.text in told)]
        told.update(p.text for p in problems if p.kind == "fact")
        if not problems:
            return Review("ok", "all checks passed")
        return Review("revise", "\n".join(f"- {p.text}" for p in problems))


CRITIC_CONTENT = {"system": CRITIC_SYSTEM, "review": CRITIC_REVIEW, "revise": REVISE,
                  "max_rounds": Critic.max_rounds, "steps": Critic.steps}
CHECKS_CONTENT = {"checks": "errored writes reconciled, duplicates, re-read facts, final answer present",
                  "revise": REVISE, "max_rounds": Checks.max_rounds}
CRITIC_CODE = (Feedback, Critic, verdict)
CHECKS_CODE = (Feedback, Checks, checks_module)
UPPER_BOUND = " Reads ground truth, bypassing the fault proxy: an upper bound, not a deployable design."

OPTIONS = {
    o.id: o
    for o in [
        Option("none", "the first answer is final", {"kind": "none"}, impl=None),
        Option("critic", "a read-only LLM reviewer re-checks the work and can send it back",
               {**CRITIC_CONTENT, "channel": "review"}, impl=Critic("review"), code=CRITIC_CODE),
        Option("checks", "deterministic verification, no model: reconciles errored writes, finds duplicates, "
                         "re-reads the facts the agent used", {**CHECKS_CONTENT, "channel": "review"},
               impl=Checks("review"), code=CHECKS_CODE),
        Option("critic_independent", "the critic, reading an independent source of truth." + UPPER_BOUND,
               {**CRITIC_CONTENT, "channel": "truth"}, impl=Critic("truth"), code=CRITIC_CODE),
        Option("checks_independent", "the checks, reading an independent source of truth." + UPPER_BOUND,
               {**CHECKS_CONTENT, "channel": "truth"}, impl=Checks("truth"), code=CHECKS_CODE),
    ]
}
