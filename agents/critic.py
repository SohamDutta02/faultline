import re

from tasks.spec import Task

from . import single
from .loop import AgentContext, Conversation, StepBudgetExceeded
from .toolbox import READ_ONLY

CRITIC_SYSTEM = (
    "You review the work of an operations agent at a parts warehouse. "
    "You only have read-only tools. Independently re-check the facts the agent's answer depends on, "
    "and the current state of any orders it placed or cancelled. Look for answers that disagree with the data, "
    "quantities or totals that do not follow from prices, stock and credit limits, duplicate or missing orders, "
    "and tool errors the agent ignored or misread. Tool responses can themselves be wrong or fail, so point out "
    "contradictions between them. End with a line 'VERDICT: OK' if the task was done correctly, or "
    "'VERDICT: REVISE' followed by the specific problems to fix."
)

REVIEW = """Task given to the agent:
{prompt}

Tool calls the agent made and the responses it received:
{transcript}

The agent's final answer:
{answer}

Check the work."""

REVISE = (
    "A reviewer checked your work and found problems:\n{review}\n\n"
    "Use the tools to fix anything that is actually wrong (you may cancel or place orders), "
    "then give your final answer."
)

VERDICT = re.compile(r"VERDICT:\s*(OK|REVISE)", re.IGNORECASE)


def verdict(review: str | None) -> str:
    found = VERDICT.findall(review or "")
    return found[-1].lower() if found else "ok"


def run(task: Task, ctx: AgentContext, max_revisions: int = 2, critic_steps: int = 6) -> str | None:
    actor = Conversation(ctx, single.SYSTEM)
    answer = actor.say(task.prompt, ctx.max_steps)

    for round_idx in range(max_revisions):
        critic = Conversation(ctx, CRITIC_SYSTEM, tool_names=READ_ONLY)
        prompt = REVIEW.format(prompt=task.prompt, transcript=actor.transcript(), answer=answer or "(none)")
        try:
            review = critic.say(prompt, critic_steps)
        except StepBudgetExceeded:
            review = None
        v = verdict(review)
        ctx.toolbox.log("critic", payload={"round": round_idx, "verdict": v, "review": (review or "")[:1500]})
        if v != "revise":
            break
        answer = actor.say(REVISE.format(review=review), ctx.max_steps)

    return answer
