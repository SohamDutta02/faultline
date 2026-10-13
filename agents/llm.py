"""Provider-neutral chat interface used by every agent architecture.

Conversations are lists of plain dicts:
  {"role": "user", "text": str}
  {"role": "assistant", "reply": Reply}
  {"role": "tool", "results": [{"call": ToolCall, "body": str, "is_error": bool}]}

A backend turns that history into a provider request and returns a Reply.
Reply.raw keeps the provider's native message so it can be replayed verbatim
(Gemini thinking models need their thought signatures sent back).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import settings


class LLMError(Exception):
    """The model API failed (quota, network, bad request). Not the agent's fault."""


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str | None = None


@dataclass
class Reply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    tokens: int = 0
    finish: str | None = None
    raw: Any = None


class Backend(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> Reply: ...


def tool_payload(body: str) -> dict:
    try:
        payload = json.loads(body)
    except Exception:
        return {"raw": body, "parse_error": True}
    return payload if isinstance(payload, dict) else {"result": payload}


class GeminiBackend:
    MAX_RETRIES = 4
    BASE_DELAY = 8

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 1024):
        key = api_key or settings.gemini_key()
        if not key:
            raise LLMError("GEMINI_API_KEY missing (set it in .env)")

        from google import genai
        from google.genai import types

        self.types = types
        self.client = genai.Client(api_key=key)
        self.model = model or settings.MODEL
        self.max_tokens = max_tokens

    def _tools(self, tools: list[dict]):
        t = self.types
        decls = [
            t.FunctionDeclaration(name=s["name"], description=s["description"], parameters=s["input_schema"])
            for s in tools
        ]
        return [t.Tool(function_declarations=decls)]

    def contents(self, messages: list[dict]) -> list:
        t = self.types
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append(t.Content(role="user", parts=[t.Part(text=m["text"])]))
            elif m["role"] == "assistant":
                reply: Reply = m["reply"]
                if reply.raw is not None:
                    out.append(reply.raw)
                    continue
                parts = [t.Part(text=reply.text)] if reply.text else []
                parts += [
                    t.Part(function_call=t.FunctionCall(id=c.id, name=c.name, args=c.args))
                    for c in reply.calls
                ]
                out.append(t.Content(role="model", parts=parts))
            elif m["role"] == "tool":
                parts = [
                    t.Part(
                        function_response=t.FunctionResponse(
                            id=r["call"].id, name=r["call"].name, response=tool_payload(r["body"])
                        )
                    )
                    for r in m["results"]
                ]
                out.append(t.Content(role="user", parts=parts))
        return self._merge_user_turns(out)

    def _merge_user_turns(self, contents: list) -> list:
        # A follow-up instruction can land right after tool results (e.g. the
        # critic asking for a revision); keep turns alternating by folding it
        # into the same user turn.
        merged = []
        for c in contents:
            if merged and c.role == "user" and merged[-1].role == "user":
                merged[-1] = self.types.Content(role="user", parts=list(merged[-1].parts) + list(c.parts))
            else:
                merged.append(c)
        return merged

    def parse(self, resp) -> Reply:
        tokens = 0
        usage = resp.usage_metadata
        if usage:
            tokens = (
                (usage.prompt_token_count or 0)
                + (usage.candidates_token_count or 0)
                + (usage.thoughts_token_count or 0)
            )
        if not resp.candidates:
            return Reply(tokens=tokens, finish="blocked")

        cand = resp.candidates[0]
        content = cand.content
        parts = list(content.parts or []) if content else []
        calls = [
            ToolCall(name=p.function_call.name, args=dict(p.function_call.args or {}), id=p.function_call.id)
            for p in parts
            if p.function_call
        ]
        text = "".join(p.text for p in parts if p.text and not p.thought).strip()
        finish = getattr(cand.finish_reason, "name", cand.finish_reason)
        return Reply(text=text, calls=calls, tokens=tokens, finish=finish, raw=content if parts else None)

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> Reply:
        t = self.types
        config = t.GenerateContentConfig(
            system_instruction=system,
            tools=self._tools(tools),
            max_output_tokens=self.max_tokens,
            temperature=0,
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = self.contents(messages)

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as exc:
                msg = str(exc)
                transient = "RESOURCE_EXHAUSTED" in msg or "503" in msg or "UNAVAILABLE" in msg
                if transient and "PerDay" not in msg and attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_DELAY * (2 ** attempt))
                    continue
                raise LLMError(f"{type(exc).__name__}: {msg}") from exc
            return self.parse(resp)


class ScriptedBackend:
    """Replays canned replies; each step is a Reply or a callable(messages) -> Reply.

    Used by the tests and for dry runs of an architecture without an API key.
    """

    model = "scripted"

    def __init__(self, steps: list[Reply | Callable[[list[dict]], Reply]]):
        self.steps = list(steps)
        self.requests: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> Reply:
        self.requests.append({"messages": list(messages), "tools": [t["name"] for t in tools], "system": system})
        if not self.steps:
            raise LLMError("scripted backend ran out of replies")
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


_default: Backend | None = None


def default_backend() -> Backend:
    global _default
    if _default is None:
        _default = GeminiBackend()
    return _default
