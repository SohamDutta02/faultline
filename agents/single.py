from tasks.spec import Task

from .loop import AgentContext, Conversation

SYSTEM = (
    "You are an operations agent for a parts warehouse. "
    "Use the provided tools to complete the user's request. "
    "Verify facts with tools rather than assuming them. "
    "When the task is complete, reply with a short plain-text answer and no further tool calls. "
    "If asked for a number, reply with just that number."
)


def run(task: Task, ctx: AgentContext) -> str | None:
    return Conversation(ctx, SYSTEM).say(task.prompt, ctx.max_steps)
