"""OpenAI over the Responses API: the catalog by key, the request (stateless, encrypted reasoning, efforts each model
takes, tools not strict), the streamed answer, OpenAI's own items replayed, a long prompt's price. The API is a mock:
nothing leaves this machine."""

import asyncio
import json

import httpx
import pytest

from hub.gateway.base import ProviderError, cost_usd
from hub.gateway.openai_responses import MODELS, OpenAIProvider, effort_for, to_input

KEY = "sk-test-openai-123456"
TOOL = {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {}}}
REASONING = {"type": "reasoning", "id": "rs_1", "encrypted_content": "gAAAA-opaque", "summary": [
    {"type": "summary_text", "text": "Прочитаю файл."}]}  # fmt: skip
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read_file", "arguments": '{"path":"a.py"}'}


def events(*items):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in items)


class Api:
    def __init__(self, answer="", status=200):
        self.answer = answer
        self.status = status
        self.sent = []

    def __call__(self, request):
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "gpt-6.1-sol"}, {"id": "gpt-5.5"},
                                                                        {"id": "dall-e-9"}]})  # fmt: skip
        self.sent.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "Incorrect API key provided"}})
        return httpx.Response(200, text=self.answer, headers={"content-type": "text/event-stream"})


def provider(api):
    return OpenAIProvider(lambda: KEY, transport=httpx.MockTransport(api))


def collect(gen):
    async def run():
        return [e async for e in gen]

    return asyncio.run(run())


def test_the_catalog():
    models = asyncio.run(provider(Api()).list_models())
    assert [m.id for m in models] == ["openai/gpt-6.1-sol", "openai/gpt-5.5"]
    assert models[0].prices == MODELS["gpt-6.1-sol"][1] and models[0].locality == "cloud"
    assert asyncio.run(OpenAIProvider(lambda: None).list_models()) == []


def test_efforts_each_model_takes():
    assert effort_for("gpt-6.1-sol", "max") == "max"
    assert effort_for("gpt-5.5", "max") == "xhigh"  # 5.5 stops at xhigh
    assert effort_for("gpt-6-astra", "low") == "low"
    assert effort_for("gpt-unknown", "medium") == "medium"


def test_a_streamed_answer_with_a_call():
    answer = events(
        {"type": "response.created", "response": {"status": "in_progress"}},
        {"type": "response.reasoning_summary_text.delta", "delta": "Прочитаю "},
        {"type": "response.reasoning_summary_text.delta", "delta": "файл."},
        {"type": "response.output_item.done", "item": REASONING},
        {"type": "response.output_text.delta", "delta": "Смотрю a.py"},
        {"type": "response.output_item.done", "item": {"type": "message", "id": "msg_1", "role": "assistant",
                                                       "content": [{"type": "output_text", "text": "Смотрю a.py"}]}},
        {"type": "response.output_item.done", "item": CALL},
        {"type": "response.completed", "response": {"status": "completed", "usage": {
            "input_tokens": 1200, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 80,
            "output_tokens_details": {"reasoning_tokens": 50}}}},
    )  # fmt: skip
    api = Api(answer)
    messages = [{"role": "system", "content": "Роль."}, {"role": "user", "content": "что в a.py?"}]
    got = collect(provider(api).stream_chat("gpt-5.5", messages, "max", asyncio.Event(), tools=[TOOL]))
    sent = api.sent[0]
    assert sent["store"] is False and sent["stream_options"] == {"include_obfuscation": False}
    assert sent["reasoning"] == {"effort": "xhigh", "summary": "auto"} and sent["instructions"] == "Роль."
    assert sent["input"] == [{"role": "user", "content": "что в a.py?"}]
    assert sent["tools"] == [{"type": "function", "name": "read_file", "description": "Read a file",
                              "parameters": TOOL["parameters"], "strict": False}]  # fmt: skip
    assert [(e.kind, e.text) for e in got[:-1]] == [("reasoning", "Прочитаю "), ("reasoning", "файл."),
                                                    ("content", "Смотрю a.py")]  # fmt: skip
    end = got[-1]
    assert end.tool_calls == [{"id": "call_1", "name": "read_file", "input": {"path": "a.py"}}]
    assert end.usage == {"input_tokens": 1200, "cached_input_tokens": 1000, "cache_write_tokens": 0,
                         "output_tokens": 80}  # fmt: skip
    assert [i["type"] for i in end.raw["output"]] == ["reasoning", "message", "function_call"]
    assert end.refusal is None and end.stop_reason == "completed"


def test_a_conversation_keeps_one_cache_key():
    """`prompt_cache_key` (D46, step 13.9): every request of a conversation carries the same key - its history only
    grows - so OpenAI routes them to its cached prefix; another conversation has its own. Made from the
    instructions and the first message: the adapter does not know the conversation's id, and conversations that
    start the same share a prefix anyway."""
    done = events({"type": "response.completed", "response": {"status": "completed", "usage": {}}})

    def key_of(messages):
        api = Api(done)
        collect(provider(api).stream_chat("gpt-6.1-sol", messages, "medium", asyncio.Event()))
        return api.sent[0]["prompt_cache_key"]

    first = [{"role": "system", "content": "Роль."}, {"role": "user", "content": "что в a.py?"}]
    later = [*first, {"role": "assistant", "content": "print(1)"}, {"role": "user", "content": "а в b.py?"}]
    key = key_of(first)
    assert key == key_of(later) and len(key) <= 64
    assert key != key_of([{"role": "system", "content": "Роль."}, {"role": "user", "content": "другое"}])
    assert key != key_of([{"role": "system", "content": "Другая роль."}, {"role": "user", "content": "что в a.py?"}])


def test_history_on_the_wire():
    own = {"output": [REASONING, CALL]}
    messages = [
        {"role": "system", "content": "Роль."},
        {"role": "user", "content": "что в a.py?"},
        {"role": "system", "content": "Projects: LocalChat"},
        {"role": "assistant", "content": "", "provider": "openai", "provider_raw": own,
         "tool_calls": [{"id": "call_1", "name": "read_file", "input": {"path": "a.py"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "print(1)"},
        {"role": "assistant", "content": "Смотрю ещё.", "provider": "anthropic", "provider_raw": {"content": []},
         "tool_calls": [{"id": "toolu_2", "name": "git_status", "input": {}}]},
        {"role": "tool", "tool_call_id": "toolu_2", "name": "git_status", "content": ""},
    ]  # fmt: skip
    instructions, items = to_input(messages, "openai")
    assert instructions == "Роль."
    assert items == [
        {"role": "user", "content": "что в a.py?"},
        {"role": "developer", "content": "Projects: LocalChat"},
        REASONING, CALL,  # its own items, the encrypted reasoning included, as received
        {"type": "function_call_output", "call_id": "call_1", "output": "print(1)"},
        {"role": "assistant", "content": "Смотрю ещё."},
        {"type": "function_call", "call_id": "toolu_2", "name": "git_status", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "toolu_2", "output": "(empty)"},
    ]  # fmt: skip


def test_a_long_prompt_costs_more():
    usage = {"input_tokens": 300_000, "input_tokens_details": {"cached_tokens": 100_000}, "output_tokens": 1000}
    answer = events({"type": "response.completed", "response": {"status": "completed", "usage": usage}})
    end = collect(provider(Api(answer)).stream_chat("gpt-6.1-sol", [{"role": "user", "content": "x"}], "low",
                                                    asyncio.Event()))[-1]  # fmt: skip
    # 200K fresh x $4 + 100K cached x $0.20 + 1K out x $15 (per 1M) = 0.8 + 0.02 + 0.015
    assert cost_usd(end.usage, MODELS["gpt-6.1-sol"][1]) == pytest.approx(0.835)


def test_refusals_and_errors():
    cut = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "usage": {"input_tokens": 5}}
    stopped = events({"type": "response.incomplete", "response": cut})
    end = collect(provider(Api(stopped)).stream_chat("gpt-5.5", [{"role": "user", "content": "x"}], "low",
                                                     asyncio.Event()))[-1]  # fmt: skip
    assert end.stop_reason == "max_tokens"
    refused = events(
        {"type": "response.refusal.delta", "delta": "I can't help with that."},
        {"type": "response.completed", "response": {"status": "completed", "usage": {}}},
    )
    end = collect(provider(Api(refused)).stream_chat("gpt-5.5", [{"role": "user", "content": "x"}], "low",
                                                     asyncio.Event()))[-1]  # fmt: skip
    assert end.refusal == "OpenAI declined: I can't help with that."
    failed = events({"type": "response.failed", "response": {"error": {"message": "server overloaded"}}})
    with pytest.raises(ProviderError, match="server overloaded"):
        collect(provider(Api(failed)).stream_chat("gpt-5.5", [{"role": "user", "content": "x"}], "low",
                                                  asyncio.Event()))  # fmt: skip
    with pytest.raises(ProviderError, match=r"the API key was rejected \(401\)"):
        collect(provider(Api(status=401)).stream_chat("gpt-5.5", [{"role": "user", "content": "x"}], "low",
                                                      asyncio.Event()))  # fmt: skip
