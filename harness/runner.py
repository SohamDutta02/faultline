"""Run one task under one harness: the actor loop, then feedback rounds."""

from agents.loop import AgentContext, Conversation
from tasks.spec import Task

from .feedback import REVISE
from .instructions import system_prompt
from .spec import HarnessSpec
from .state import make_state


def run(task: Task, ctx: AgentContext, spec: HarnessSpec) -> str | None:
    opts = spec.options()
    actor = Conversation(ctx, system_prompt(spec.instructions), state=make_state(spec.state))
    answer = actor.say(task.prompt, ctx.max_steps)

    feedback = opts["feedback"].impl
    if feedback is None:
        return answer

    # grade the first answer before feedback touches it, so the trial records
    # what feedback actually changed
    ctx.checkpoint("first_pass", answer)
    for round_idx in range(feedback.max_rounds):
        review = feedback.review(task, ctx, actor, answer)
        ctx.reviews.append({"round": round_idx, "verdict": review.verdict})
        ctx.toolbox.log("feedback", payload={
            "source": spec.feedback, "round": round_idx, "verdict": review.verdict, "review": review.message[:1500],
        })
        if review.verdict != "revise":
            break
        answer = actor.say(REVISE.format(review=review.message), ctx.max_steps)
    return answer
