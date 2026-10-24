"""The Anthropic and OpenAI-compatible backends and provider routing, without touching the network."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function

from agents import backends
from agents.anthropic_backend import AnthropicBackend
from agents.llm import LLMError, QuotaExhausted, Reply, ToolCall
from agents.openai_backend import OpenAIBackend
from runner.trial import run_trial
from tasks.catalog import BY_ID

TOOLS = [{"name": "search_inventory", "description": "Look up stock.",
          "input_schema": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}}]
CALL = ToolCall("search_inventory", {"sku": "A-22"}, id="tu_1")
HISTORY = [
    {"role": "user", "text": "how many?"},
    {"role": "assistant", "reply": Reply(text="Checking.", calls=[CALL])},
    {"role": "tool", "results": [{"call": CALL, "body": '{"error": "rate_limited"}', "is_error": True}]},
    {"role": "user", "text": "please revise"},
]


def status_error(cls, code: int, url: str):
    return cls("boom", response=httpx.Response(code, request=httpx.Request("POST", url)), body=None)


class FakeClient:
    """Stands in for an SDK client: records requests, answers from a script of
    responses or callables(request) -> response."""

    def __init__(self, *responses, method="messages"):
        self.responses = list(responses)
        self.requests = []
        api = SimpleNamespace(create=self.create)
        if method == "messages":
            self.messages = api
        else:
            self.chat = SimpleNamespace(completions=api)

    def create(self, **request):
        self.requests.append(request)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r(request) if callable(r) else r


# ---- Anthropic ------------------------------------------------------------------

def message(*blocks, stop="end_turn", usage=(10, 5)):
    return Message.model_construct(
        id="msg", type="message", role="assistant", model="claude-test", content=list(blocks), stop_reason=stop,
        usage=Usage.model_construct(input_tokens=usage[0], output_tokens=usage[1]))


def text(t):
    return TextBlock.model_construct(type="text", text=t)


def tool_use(name, args, id_="tu_1"):
    return ToolUseBlock.model_construct(type="tool_use", id=id_, name=name, input=args)


def test_anthropic_messages_keep_turns_alternating():
    msgs = AnthropicBackend(client=FakeClient()).messages(HISTORY)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == [{"type": "text", "text": "Checking."},
                                  {"type": "tool_use", "id": "tu_1", "name": "search_inventory",
                                   "input": {"sku": "A-22"}}]
    result, follow_up = msgs[2]["content"]
    assert result == {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"error": "rate_limited"}',
                      "is_error": True}
    assert follow_up == {"type": "text", "text": "please revise"}  # after the results, same turn


def test_anthropic_parse_and_replay():
    reply = AnthropicBackend.parse(message(text("Let me check."), tool_use("search_inventory", {"sku": "A-22"}),
                                           stop="tool_use"))
    assert reply.text == "Let me check." and reply.calls == [CALL] and reply.tokens == 15
    assert reply.finish == "tool_use"
    msgs = AnthropicBackend(client=FakeClient()).messages([{"role": "assistant", "reply": reply}])
    assert msgs[0]["content"] == reply.raw and reply.raw[1]["id"] == "tu_1"


def test_anthropic_request_shape():
    client = FakeClient(message(text("12")))
    reply = AnthropicBackend(model="claude-x", client=client).complete([{"role": "user", "text": "q"}], TOOLS, "sys")
    (req,) = client.requests
    assert reply.text == "12"
    assert (req["model"], req["system"], req["max_tokens"]) == ("claude-x", "sys", 1024)
    assert req["tools"] == TOOLS and "temperature" not in req


def test_anthropic_retries_transient_errors_and_stops_on_quota(monkeypatch):
    monkeypatch.setattr(AnthropicBackend, "BASE_DELAY", 0)
    url = "https://api.anthropic.com/v1/messages"
    client = FakeClient(status_error(anthropic.RateLimitError, 429, url),
                        status_error(anthropic.OverloadedError, 529, url), message(text("ok")))
    assert AnthropicBackend(client=client).complete([{"role": "user", "text": "q"}], [], "s").text == "ok"
    assert len(client.requests) == 3

    broke = FakeClient(anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API",
        response=httpx.Response(400, request=httpx.Request("POST", url)), body=None))
    with pytest.raises(QuotaExhausted, match="credit balance"):
        AnthropicBackend(client=broke).complete([{"role": "user", "text": "q"}], [], "s")
    assert len(broke.requests) == 1

    denied = FakeClient(status_error(anthropic.AuthenticationError, 401, url))
    with pytest.raises(LLMError, match="AuthenticationError"):
        AnthropicBackend(client=denied).complete([{"role": "user", "text": "q"}], [], "s")


def test_anthropic_needs_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        AnthropicBackend()


# ---- OpenAI-compatible ----------------------------------------------------------------

def completion(content=None, calls=(), finish="stop", total=30):
    tool_calls = [ChatCompletionMessageToolCall.model_construct(
        id=i, type="function", function=Function.model_construct(name=n, arguments=a)) for i, n, a in calls]
    return ChatCompletion.model_construct(
        id="c", object="chat.completion", created=0, model="m",
        choices=[Choice.model_construct(index=0, finish_reason=finish, message=ChatCompletionMessage.model_construct(
            role="assistant", content=content, tool_calls=tool_calls or None))],
        usage=CompletionUsage.model_construct(prompt_tokens=20, completion_tokens=10, total_tokens=total))


def oai(client, base_url="http://localhost:8000/v1", model="Qwen/Qwen3-8B"):
    return OpenAIBackend(model=model, base_url=base_url, client=client)


def test_openai_messages():
    msgs = oai(FakeClient(method="chat")).messages(HISTORY, "sys")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "user"]
    assert msgs[2]["tool_calls"] == [{"id": "tu_1", "type": "function",
                                      "function": {"name": "search_inventory", "arguments": '{"sku": "A-22"}'}}]
    assert msgs[3] == {"role": "tool", "tool_call_id": "tu_1", "content": '{"error": "rate_limited"}'}


def test_openai_parse_keeps_unparseable_arguments():
    reply = OpenAIBackend.parse(completion(calls=[("c1", "search_inventory", '{"sku": "A-22"}'),
                                                  ("c2", "get_customer", '{"id": "CUST-1"')], finish="tool_calls"))
    assert reply.calls[0] == ToolCall("search_inventory", {"sku": "A-22"}, "c1")
    assert reply.calls[1].args == {"_unparsed": '{"id": "CUST-1"'} and reply.tokens == 30
    # replayed exactly as the model sent it
    msgs = oai(FakeClient(method="chat")).messages([{"role": "assistant", "reply": reply}], "s")
    assert msgs[1]["tool_calls"][1]["function"]["arguments"] == '{"id": "CUST-1"'
    assert OpenAIBackend.parse(ChatCompletion.model_construct(choices=[], usage=None)).finish == "empty"


def test_openai_official_vs_local_request():
    local = FakeClient(completion("12"), method="chat")
    oai(local).complete([{"role": "user", "text": "q"}], TOOLS, "sys")
    (req,) = local.requests
    assert req["max_tokens"] == 1024 and req["temperature"] == 0 and "max_completion_tokens" not in req
    assert req["tools"][0] == {"type": "function", "function": {"name": "search_inventory",
                                                                 "description": "Look up stock.",
                                                                 "parameters": TOOLS[0]["input_schema"]}}
    official = FakeClient(completion("12"), method="chat")
    OpenAIBackend(model="gpt-test", base_url="https://api.openai.com/v1", client=official).complete(
        [{"role": "user", "text": "q"}], [], "sys")
    (req,) = official.requests
    assert req["max_completion_tokens"] == 1024 and "temperature" not in req and "tools" not in req


def test_openai_errors(monkeypatch):
    monkeypatch.setattr(OpenAIBackend, "BASE_DELAY", 0)
    url = "https://api.openai.com/v1/chat/completions"
    client = FakeClient(status_error(openai.RateLimitError, 429, url), completion("ok"), method="chat")
    assert oai(client).complete([{"role": "user", "text": "q"}], [], "s").text == "ok"
    quota = FakeClient(openai.RateLimitError(
        "You exceeded your current quota", response=httpx.Response(429, request=httpx.Request("POST", url)),
        body={"code": "insufficient_quota"}), method="chat")
    with pytest.raises(QuotaExhausted):
        oai(quota).complete([{"role": "user", "text": "q"}], [], "s")
    with pytest.raises(LLMError, match="no model"):
        OpenAIBackend(model=None, client=FakeClient(method="chat"))


def test_openai_key_rules(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        OpenAIBackend(model="gpt-test", base_url="https://api.openai.com/v1")
    assert OpenAIBackend(model="llama3.1", base_url="http://localhost:11434/v1").model == "llama3.1"  # no key needed


# ---- routing ------------------------------------------------------------------------

@pytest.mark.parametrize("model, provider", [
    ("gemini-2.5-flash", "gemini"), ("claude-haiku-5-5", "anthropic"), ("gpt-5-mini", "openai"),
    ("o4-mini", "openai"), ("Qwen/Qwen3-8B", "openai"), ("llama3.1", "openai"),
])
def test_model_ids_pick_their_provider(model, provider):
    assert backends.provider_for(model) == provider


def configure(monkeypatch, model=None, provider=None, **keys):
    monkeypatch.setattr(backends.settings, "MODEL", model)
    monkeypatch.setattr(backends.settings, "PROVIDER", provider)
    for var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    for var, value in keys.items():
        monkeypatch.setenv(var, value)


def test_default_model_follows_the_keys_you_have(monkeypatch):
    configure(monkeypatch)
    assert backends.resolve() == ("gemini", "gemini-2.0-flash") and not backends.available()
    configure(monkeypatch, ANTHROPIC_API_KEY="k")
    assert backends.resolve() == ("anthropic", "claude-haiku-5-5") and backends.available()
    configure(monkeypatch, model="claude-sonnet-5-5", GEMINI_API_KEY="g", ANTHROPIC_API_KEY="k")
    assert backends.resolve() == ("anthropic", "claude-sonnet-5-5")
    assert backends.resolve(provider="gemini") == ("gemini", "gemini-2.0-flash")
    configure(monkeypatch, provider="openai", OPENAI_BASE_URL="http://localhost:8000/v1")
    assert backends.resolve() == ("openai", None) and not backends.available()  # a local server needs a model id
    assert backends.available("Qwen/Qwen3-8B")
    with pytest.raises(LLMError, match="unknown provider"):
        backends.provider_for("x", "azure")
    rows = {r["id"]: r for r in backends.describe()}
    assert rows["openai"]["available"] and rows["openai"]["default"] and not rows["anthropic"]["available"]


# ---- whole trials through each backend -----------------------------------------------------

def test_a_trial_through_the_anthropic_backend(urls):
    toolserver_url, proxy_url = urls

    def answer(request):
        result = request["messages"][-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
        return message(text(str(json.loads(result["content"])["qty"])))

    client = FakeClient(message(tool_use("search_inventory", {"sku": "A-22"}), stop="tool_use"), answer)
    r = run_trial(BY_ID["t1_stock_lookup"], "single", AnthropicBackend(client=client), 12, toolserver_url, proxy_url)
    assert r.outcome == "pass", r.reason
    assert r.tokens_used == 30 and r.llm_calls == 2


def test_a_trial_through_an_openai_compatible_server(urls):
    toolserver_url, proxy_url = urls

    def answer(request):
        tool_msg = request["messages"][-1]
        assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
        return completion(str(json.loads(tool_msg["content"])["qty"]))

    client = FakeClient(completion(calls=[("c1", "search_inventory", '{"sku": "A-22"}')], finish="tool_calls"),
                        answer, method="chat")
    r = run_trial(BY_ID["t1_stock_lookup"], "retry", oai(client), 12, toolserver_url, proxy_url)
    assert r.outcome == "pass", r.reason


def test_api_reports_providers_and_needs_the_right_key(make_client, monkeypatch):
    configure(monkeypatch)
    client = make_client(llm_available=None)
    meta = client.get("/api/meta").json()
    assert [p["id"] for p in meta["providers"]] == ["gemini", "anthropic", "openai"]
    r = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "single",
                                         "model": "claude-haiku-5-5"})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]
