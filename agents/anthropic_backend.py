"""Claude models through the Anthropic Messages API.

The conversation maps onto Messages API turns: the system prompt goes in
`system`, assistant turns are lists of content blocks (text, tool_use), and
tool results go back in the next user turn as tool_result blocks keyed by the
tool_use id. A follow-up instruction that lands right after tool results (the
critic or checks asking for a revision) joins that same user turn, after the
results, so turns keep alternating.

The SDK retries transient failures itself; on top of that, rate limits and
overloads are retried with backoff here, and a spend or credit limit stops
the sweep instead of burning through its cells.
"""

import time

from .llm import LLMError, QuotaExhausted, Reply, ToolCall

DEFAULT_MODEL = "claude-haiku-5-5"


def _key() -> str:
    import settings

    return settings.anthropic_key()


class AnthropicBackend:
    MAX_RETRIES = 4
    BASE_DELAY = 8

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 1024,
                 client=None):
        import anthropic

        self.anthropic = anthropic
        if client is None:
            key = api_key or _key()
            if not key:
                raise LLMError("ANTHROPIC_API_KEY missing (set it in .env)")
            client = anthropic.Anthropic(api_key=key)
        self.client = client
        self.model = model or DEFAULT_MODEL
        self.max_tokens = max_tokens

    # ---- request -------------------------------------------------------------

    @staticmethod
    def tools(tools: list[dict]) -> list[dict]:
        return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                for t in tools]

    def messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []

        def user(blocks: list[dict]):
            if out and out[-1]["role"] == "user":
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": "user", "content": list(blocks)})

        for m in messages:
            if m["role"] == "user":
                user([{"type": "text", "text": m["text"]}])
            elif m["role"] == "assistant":
                reply: Reply = m["reply"]
                if reply.raw is not None:
                    out.append({"role": "assistant", "content": list(reply.raw)})
                    continue
                blocks = [{"type": "text", "text": reply.text}] if reply.text else []
                blocks += [{"type": "tool_use", "id": c.id or f"call_{i}", "name": c.name, "input": c.args}
                           for i, c in enumerate(reply.calls)]
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                user([
                    {"type": "tool_result", "tool_use_id": r["call"].id or f"call_{i}",
                     "content": r["body"] or "(empty response)", "is_error": bool(r["is_error"])}
                    for i, r in enumerate(m["results"])
                ])
        return out

    # ---- response ------------------------------------------------------------

    @staticmethod
    def parse(message) -> Reply:
        usage = message.usage
        tokens = 0
        if usage is not None:
            tokens = sum(getattr(usage, k, 0) or 0 for k in (
                "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        blocks = list(message.content or [])
        text = "".join(b.text for b in blocks if b.type == "text").strip()
        calls = [ToolCall(name=b.name, args=dict(b.input or {}), id=b.id) for b in blocks if b.type == "tool_use"]
        raw = [b.model_dump(mode="json", exclude_none=True) for b in blocks] if blocks else None
        return Reply(text=text, calls=calls, tokens=tokens, finish=message.stop_reason, raw=raw)

    # ---- call ----------------------------------------------------------------

    def _classify(self, exc: Exception) -> str:
        """'quota', 'transient' or 'fatal'."""
        a = self.anthropic
        msg = str(exc).lower()
        if "credit balance" in msg or "spend limit" in msg or "usage limit" in msg or "billing" in msg:
            return "quota"
        transient = tuple(getattr(a, n) for n in (
            "RateLimitError", "OverloadedError", "ServiceUnavailableError", "InternalServerError",
            "APIConnectionError", "APITimeoutError") if hasattr(a, n))
        return "transient" if isinstance(exc, transient) else "fatal"

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> Reply:
        request = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": self.messages(messages),
        }
        if tools:
            request["tools"] = self.tools(tools)
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                message = self.client.messages.create(**request)
            except Exception as exc:
                kind = self._classify(exc)
                detail = f"{type(exc).__name__}: {exc}"
                if kind == "quota":
                    raise QuotaExhausted(detail) from exc
                if kind == "transient" and attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_DELAY * (2 ** attempt))
                    continue
                raise LLMError(detail) from exc
            return self.parse(message)
        raise LLMError("unreachable")  # pragma: no cover
