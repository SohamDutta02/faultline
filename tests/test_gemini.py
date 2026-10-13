"""GeminiBackend translation and error handling, without touching the network."""

import pytest
from google.genai import types

from agents import llm
from agents.llm import GeminiBackend, LLMError, Reply, ToolCall


@pytest.fixture
def backend():
    return GeminiBackend(model="test-model", api_key="test-key")


def response(parts, finish="STOP", usage=(10, 4, 0)):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0], candidates_token_count=usage[1], thoughts_token_count=usage[2]
        ),
    )


def test_missing_key(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_key", lambda: "")
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiBackend()


def test_parse_tool_calls_and_usage(backend):
    resp = response(
        [
            types.Part(text="thinking...", thought=True),
            types.Part(function_call=types.FunctionCall(name="search_inventory", args={"sku": "A-22"})),
        ],
        usage=(10, 4, 3),
    )
    reply = backend.parse(resp)
    assert reply.calls == [ToolCall("search_inventory", {"sku": "A-22"}, None)]
    assert reply.text == ""
    assert reply.tokens == 17
    assert reply.finish == "STOP"
    assert reply.raw is resp.candidates[0].content


def test_parse_blocked_prompt(backend):
    reply = backend.parse(types.GenerateContentResponse(candidates=[]))
    assert reply.finish == "blocked" and not reply.calls and reply.text == ""


def test_contents_round_trip(backend):
    raw = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="x", args={}))])
    call = ToolCall("search_inventory", {"sku": "A-22"}, id="c1")
    messages = [
        {"role": "user", "text": "how many?"},
        {"role": "assistant", "reply": Reply(raw=raw)},
        {"role": "tool", "results": [{"call": call, "body": "{not json", "is_error": False}]},
        {"role": "user", "text": "please revise"},
        {"role": "assistant", "reply": Reply(text="ok", calls=[call])},
    ]
    contents = backend.contents(messages)
    assert [c.role for c in contents] == ["user", "model", "user", "model"]
    assert contents[1] is raw  # replayed verbatim, keeps thought signatures
    fr = contents[2].parts[0].function_response
    assert (fr.id, fr.name, fr.response) == ("c1", "search_inventory", {"raw": "{not json", "parse_error": True})
    assert contents[2].parts[1].text == "please revise"
    assert contents[3].parts[0].text == "ok"
    assert contents[3].parts[1].function_call.args == {"sku": "A-22"}


def test_complete_returns_parsed_reply(backend, monkeypatch):
    seen = {}

    def fake(model, contents, config):
        seen.update(model=model, n=len(contents), tools=config.tools[0].function_declarations)
        return response([types.Part(text="11")])

    monkeypatch.setattr(backend.client.models, "generate_content", fake)
    tools = [{"name": "search_inventory", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    reply = backend.complete([{"role": "user", "text": "q"}], tools, "sys")
    assert reply.text == "11"
    assert seen["model"] == "test-model" and seen["n"] == 1
    assert seen["tools"][0].name == "search_inventory"


def test_transient_errors_retry_then_daily_quota_fails_fast(backend, monkeypatch):
    monkeypatch.setattr(GeminiBackend, "BASE_DELAY", 0)
    attempts = []

    def flaky(**kw):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("503 UNAVAILABLE")
        return response([types.Part(text="ok")])

    monkeypatch.setattr(backend.client.models, "generate_content", flaky)
    assert backend.complete([{"role": "user", "text": "q"}], [], "sys").text == "ok"
    assert len(attempts) == 3

    attempts.clear()

    def quota(**kw):
        attempts.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel")

    monkeypatch.setattr(backend.client.models, "generate_content", quota)
    with pytest.raises(LLMError, match="PerDay"):
        backend.complete([{"role": "user", "text": "q"}], [], "sys")
    assert len(attempts) == 1
