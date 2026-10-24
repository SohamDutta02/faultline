"""Any OpenAI-compatible chat completions endpoint: OpenAI itself, or a model
you serve yourself with vLLM, SGLang, Ollama or llama.cpp, or a hosted
router such as OpenRouter or Groq.

Point it at a server with OPENAI_BASE_URL (e.g. http://localhost:8000/v1 for
`vllm serve`); a local server usually needs no key. The model id is whatever
that server calls the model. Tool calling has to be enabled on the server
(for vLLM: --enable-auto-tool-choice and a --tool-call-parser for the model).

Tool calls arrive with JSON-string arguments. When a model emits arguments
that don't parse, the call is kept with the raw string so the toolserver
rejects it and the model sees the error, as it would in production.
"""

import json
import time

from .llm import LLMError, QuotaExhausted, Reply, ToolCall

OPENAI_URL = "https://api.openai.com/v1"


def _settings():
    import settings

    return settings


class OpenAIBackend:
    MAX_RETRIES = 4
    BASE_DELAY = 8

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None,
                 max_tokens: int = 1024, temperature: float | None = None, client=None):
        import openai

        self.openai = openai
        s = _settings()
        self.base_url = (base_url or s.openai_base_url() or OPENAI_URL).rstrip("/")
        self.official = self.base_url == OPENAI_URL
        if not model:
            raise LLMError("no model for the OpenAI-compatible backend: set FAULTLINE_MODEL or pass --model")
        if client is None:
            key = api_key or s.openai_key()
            if not key and self.official:
                raise LLMError("OPENAI_API_KEY missing (set it in .env, or OPENAI_BASE_URL for a local server)")
            client = openai.OpenAI(api_key=key or "not-needed", base_url=self.base_url)
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        # OpenAI's reasoning models reject a temperature; local servers take 0 for repeatable runs
        self.temperature = temperature if temperature is not None else (None if self.official else 0.0)

    # ---- request -------------------------------------------------------------

    @staticmethod
    def tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in tools]

    @staticmethod
    def _arguments(call: ToolCall) -> str:
        if set(call.args) == {"_unparsed"}:
            return call.args["_unparsed"]
        return json.dumps(call.args)

    def messages(self, messages: list[dict], system: str) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                reply: Reply = m["reply"]
                msg: dict = {"role": "assistant", "content": reply.text or None}
                if reply.calls:
                    msg["tool_calls"] = [
                        {"id": c.id or f"call_{i}", "type": "function",
                         "function": {"name": c.name, "arguments": self._arguments(c)}}
                        for i, c in enumerate(reply.calls)
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out += [{"role": "tool", "tool_call_id": r["call"].id or f"call_{i}", "content": r["body"]}
                        for i, r in enumerate(m["results"])]
        return out

    # ---- response ------------------------------------------------------------

    @staticmethod
    def parse(completion) -> Reply:
        usage = completion.usage
        tokens = (usage.total_tokens or 0) if usage is not None else 0
        if not completion.choices:
            return Reply(tokens=tokens, finish="empty")
        choice = completion.choices[0]
        message = choice.message
        calls = []
        for i, tc in enumerate(message.tool_calls or []):
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            try:
                args = json.loads(fn.arguments or "{}")
                if not isinstance(args, dict):
                    args = {"_unparsed": fn.arguments}
            except ValueError:
                args = {"_unparsed": fn.arguments}
            calls.append(ToolCall(name=fn.name, args=args, id=tc.id or f"call_{i}"))
        return Reply(text=(message.content or "").strip(), calls=calls, tokens=tokens, finish=choice.finish_reason)

    # ---- call ----------------------------------------------------------------

    def _classify(self, exc: Exception) -> str:
        o = self.openai
        msg = str(exc).lower()
        if "insufficient_quota" in msg or "exceeded your current quota" in msg or "billing" in msg:
            return "quota"
        transient = tuple(getattr(o, n) for n in (
            "RateLimitError", "InternalServerError", "APIConnectionError", "APITimeoutError") if hasattr(o, n))
        return "transient" if isinstance(exc, transient) else "fatal"

    def complete(self, messages: list[dict], tools: list[dict], system: str) -> Reply:
        request = {"model": self.model, "messages": self.messages(messages, system)}
        # OpenAI's newer models only take max_completion_tokens; most local servers know max_tokens
        request["max_completion_tokens" if self.official else "max_tokens"] = self.max_tokens
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if tools:
            request["tools"] = self.tools(tools)
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                completion = self.client.chat.completions.create(**request)
            except Exception as exc:
                kind = self._classify(exc)
                detail = f"{type(exc).__name__}: {exc}"
                if kind == "quota":
                    raise QuotaExhausted(detail) from exc
                if kind == "transient" and attempt < self.MAX_RETRIES:
                    time.sleep(self.BASE_DELAY * (2 ** attempt))
                    continue
                raise LLMError(detail) from exc
            return self.parse(completion)
        raise LLMError("unreachable")  # pragma: no cover
