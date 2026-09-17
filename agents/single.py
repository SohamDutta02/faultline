'''
from dataclasses import dataclass

from .llm import complete
from .toolbox import Toolbox, llm_tools

SYSTEM = (
    "You are an operations agent for a parts warehouse. "
    "Use the provided tools to complete the user's request. "
    "Verify facts with tools rather than assuming them. "
    "When the task is complete, reply with a short plain-text answer and no further tool calls. "
    "If asked for a number, reply with just that number."
)


@dataclass
class AgentResult:
    answer: str | None
    error: str | None
    tool_calls: int
    retries: int
    tokens_used: int
    events: list


def run(prompt: str, toolbox: Toolbox, max_steps: int = 12) -> AgentResult:
    messages = [{"role": "user", "content": prompt}]
    tools = llm_tools()
    tokens = 0
    retries = 0

    for _ in range(max_steps):
        try:
            resp = complete(messages, tools, SYSTEM)
        except Exception as exc:
            return AgentResult(None, f"llm_error: {type(exc).__name__}", toolbox.call_count, retries, tokens, toolbox.events)

        tokens += resp.usage.input_tokens + resp.usage.output_tokens
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return AgentResult(text or None, None, toolbox.call_count, retries, tokens, toolbox.events)

        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            body, is_error = toolbox.call(block.name, dict(block.input))
            if is_error:
                retries += 1
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": body,
                "is_error": is_error,
            })
        messages.append({"role": "user", "content": results})

    return AgentResult(None, f"llm_error: {type(exc).__name__}: {exc}", toolbox.call_count, retries, tokens, toolbox.events)
'''
import json
from dataclasses import dataclass

from google.genai import types

from .llm import complete
from .toolbox import Toolbox, llm_tools

SYSTEM = (
    "You are an operations agent for a parts warehouse. "
    "Use the provided tools to complete the user's request. "
    "Verify facts with tools rather than assuming them. "
    "When the task is complete, reply with a short plain-text answer and no further tool calls. "
    "If asked for a number, reply with just that number."
)


@dataclass
class AgentResult:
    answer: str | None
    error: str | None
    tool_calls: int
    retries: int
    tokens_used: int
    events: list


def _parts(resp):
    if not resp.candidates:
        return []
    content = resp.candidates[0].content
    return list(content.parts or []) if content else []


def run(prompt: str, toolbox: Toolbox, max_steps: int = 12) -> AgentResult:
    contents = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    tools = llm_tools()
    tokens = 0
    retries = 0

    for _ in range(max_steps):
        try:
            resp = complete(contents, tools, SYSTEM)
        except Exception as exc:
            return AgentResult(None, f"llm_error: {type(exc).__name__}: {exc}", toolbox.call_count, retries, tokens, toolbox.events)

        usage = resp.usage_metadata
        if usage:
            tokens += (usage.prompt_token_count or 0) + (usage.candidates_token_count or 0)

        parts = _parts(resp)
        calls = [p.function_call for p in parts if p.function_call]

        if not calls:
            text = "".join(p.text for p in parts if p.text).strip()
            return AgentResult(text or None, None, toolbox.call_count, retries, tokens, toolbox.events)

        contents.append(types.Content(role="model", parts=parts))

        results = []
        for fc in calls:
            body, is_error = toolbox.call(fc.name, dict(fc.args or {}))
            if is_error:
                retries += 1
            try:
                payload = json.loads(body)
            except Exception:
                payload = {"raw": body, "parse_error": True}
            if not isinstance(payload, dict):
                payload = {"result": payload}
            results.append(
                types.Part(
                    function_response=types.FunctionResponse(name=fc.name, response=payload)
                )
            )
        contents.append(types.Content(role="user", parts=results))

    return AgentResult(None, "loop", toolbox.call_count, retries, tokens, toolbox.events)    