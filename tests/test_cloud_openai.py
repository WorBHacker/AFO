"""OpenAI, DeepSeek and OpenRouter (D6): catalogs by key with prices, the request each one takes, what is kept of an
answer and sent back, and the price of a request. The providers' APIs are mocks: nothing leaves this machine."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from hub.gateway.base import ProviderError, cost_usd
from hub.gateway.cloud_openai import (
    DeepSeekProvider,
    OpenRouterProvider,
    deepseek_peak,
    join_details,
    nearest_effort,
)
from tests.test_changes import finished
from tests.test_tools import hub  # noqa: F401 - the hub fixture

KEY = "sk-test-key-1234567890"


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


class Api:
    """Answers /models and /chat/completions; keeps what the chat requests sent."""

    def __init__(self, models, answer="", status=200):
        self.models = models
        self.answer = answer
        self.status = status
        self.sent = []
        self.auth = []

    def __call__(self, request):
        self.auth.append(request.headers.get("Authorization"))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": self.models})
        self.sent.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        return httpx.Response(200, text=self.answer, headers={"content-type": "text/event-stream"})

    def transport(self):
        return httpx.MockTransport(self)


def run(provider, messages, effort="medium", tools=None):
    async def go():
        return [e async for e in provider.stream_chat(provider_ref(provider), messages, effort, asyncio.Event(),
                                                      tools=tools)]  # fmt: skip

    return asyncio.run(go())


def provider_ref(provider):
    return {"deepseek": "deepseek-v4-pro", "openrouter": "qwen/qwen3.8-coder"}[provider.name]


TOOL = {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {}}}


def test_errors_are_said_briefly():
    with pytest.raises(ProviderError, match=r"the API key was rejected \(401\)"):
        run(
            DeepSeekProvider(lambda: KEY, transport=Api([], status=401).transport()), [{"role": "user", "content": "x"}]
        )
    with pytest.raises(ProviderError, match=r"no credit left"):
        run(OpenRouterProvider(lambda: KEY, chosen=lambda: _async([]), transport=Api([], status=402).transport()),
            [{"role": "user", "content": "x"}])  # fmt: skip


def test_a_failure_inside_the_stream_is_an_error():
    broken = sse({"choices": [{"delta": {"content": "Нач"}}]},
                 {"error": {"code": 502, "message": "upstream provider failed"},
                  "choices": [{"delta": {}, "finish_reason": "error"}]})  # fmt: skip
    router = OpenRouterProvider(lambda: KEY, chosen=lambda: _async([]), transport=Api([], broken).transport())
    with pytest.raises(ProviderError, match="upstream provider failed"):
        run(router, [{"role": "user", "content": "x"}])


async def _async(value):
    return value


# --- DeepSeek ---


def test_deepseek_thinking_is_kept_and_sent_back():
    answer = sse(
        {"choices": [{"delta": {"reasoning_content": "Нужно прочитать "}}]},
        {"choices": [{"delta": {"reasoning_content": "файл."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_file",
                                                                                      "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 2000, "prompt_cache_hit_tokens": 1500, "completion_tokens": 100}},
    )  # fmt: skip
    api = Api([{"id": "deepseek-v4-pro"}, {"id": "deepseek-flash"}], answer)
    peak = datetime(2026, 10, 27, 7, 30, tzinfo=UTC)  # a Tuesday, 07:30 UTC
    deepseek = DeepSeekProvider(lambda: KEY, transport=api.transport(), clock=lambda: peak)
    models = asyncio.run(deepseek.list_models())
    assert [m.model_ref for m in models] == ["deepseek-flash", "deepseek-v4-pro"]
    assert models[1].prices == {"input": 1.32, "cache_read": 0.044, "output": 3.96}  # the peak price, to be safe

    end = run(deepseek, [{"role": "user", "content": "что в a.py?"}], tools=[TOOL])[-1]
    assert api.sent[0]["thinking"] == {"type": "enabled"} and api.sent[0]["reasoning_effort"] == "medium"
    assert end.raw == {"reasoning_content": "Нужно прочитать файл."}
    assert end.usage["cached_input_tokens"] == 1500
    # peak: 500 x 1.32 + 1500 x 0.044 + 100 x 3.96 = 660 + 66 + 396 micro-USD
    assert end.usage["cost_microusd"] == 1122 and cost_usd(end.usage, {"input": 99}) == 0.001122

    history = [
        {"role": "user", "content": "что в a.py?"},
        {"role": "assistant", "content": "", "provider": "deepseek", "provider_raw": end.raw,
         "tool_calls": [{"id": "c1", "name": "read_file", "input": {}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "print(1)"},
        {"role": "assistant", "content": "Готово.", "provider": "openrouter",
         "provider_raw": {"reasoning_details": []}},
        {"role": "user", "content": "дальше"},
    ]  # fmt: skip
    run(deepseek, history)
    wire = api.sent[1]["messages"]
    # its own turn gets its thinking back; another model's turn an empty one (DeepSeek wants it on every turn)
    assert wire[1]["reasoning_content"] == "Нужно прочитать файл." and wire[3]["reasoning_content"] == ""


def test_deepseek_peak_hours():
    assert deepseek_peak(datetime(2026, 10, 27, 1, 0, tzinfo=UTC))  # Tuesday 01:00
    assert not deepseek_peak(datetime(2026, 10, 27, 4, 0, tzinfo=UTC))
    assert deepseek_peak(datetime(2026, 10, 27, 9, 59, tzinfo=UTC))
    assert not deepseek_peak(datetime(2026, 10, 25, 7, 0, tzinfo=UTC))  # Sunday


# --- OpenRouter ---

ROUTER_MODELS = [
    {"id": "qwen/qwen3.8-coder", "name": "Qwen3.8 Coder", "context_length": 262144,
     "pricing": {"prompt": "0.0000002", "completion": "0.0000008", "input_cache_read": "0.00000005"},
     "supported_parameters": ["tools", "reasoning", "max_tokens"], "architecture": {"output_modalities": ["text"]}},
    {"id": "some/no-tools", "name": "No tools", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
     "supported_parameters": ["max_tokens"]},
    {"id": "some/image-maker", "name": "Images", "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": ["tools"], "architecture": {"output_modalities": ["image"]}},
    {"id": "openrouter/auto", "name": "Auto", "pricing": {"prompt": "-1", "completion": "-1"},
     "supported_parameters": ["tools"]},
    {"id": "qwen/qwen3.8-coder:batch", "name": "Qwen3.8 Coder (batch)", "pricing": {"prompt": "0.0000001",
     "completion": "0.0000004"}, "supported_parameters": ["tools"]},
]  # fmt: skip


def test_openrouter_offers_only_the_chosen_models():
    chosen = []
    api = Api(ROUTER_MODELS)
    router = OpenRouterProvider(lambda: KEY, chosen=lambda: _async(list(chosen)), transport=api.transport())
    everything = asyncio.run(router.everything())
    assert [m["id"] for m in everything] == ["qwen/qwen3.8-coder"]  # tools, text, priced per token
    assert everything[0] == {"id": "qwen/qwen3.8-coder", "name": "Qwen3.8 Coder", "input": 0.2, "output": 0.8,
                             "cache_read": 0.05, "context": 262144, "reasoning": True, "efforts": []}  # fmt: skip
    assert asyncio.run(router.list_models()) == []  # nothing chosen yet
    chosen.append("qwen/qwen3.8-coder")
    (model,) = asyncio.run(router.list_models())
    assert model.id == "openrouter/qwen/qwen3.8-coder" and model.prices["output"] == 0.8


def test_openrouter_reasoning_details_and_cost():
    details = [{"type": "reasoning.encrypted", "data": "opaque", "id": "r1", "format": "openai-responses-v1"}]
    answer = sse(
        {"choices": [{"delta": {"reasoning": "Думаю.", "reasoning_details": details}}]},
        {"choices": [{"delta": {"content": "Ответ"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.000123}},
    )  # fmt: skip
    api = Api(ROUTER_MODELS, answer)
    router = OpenRouterProvider(lambda: KEY, chosen=lambda: _async([]), transport=api.transport())
    events = run(router, [{"role": "user", "content": "привет"}], effort="xhigh")
    assert api.sent[0]["reasoning"] == {"effort": "xhigh"} and "reasoning_effort" not in api.sent[0]
    assert "usage" not in api.sent[0] and "stream_options" not in api.sent[0]  # OpenRouter reports usage anyway
    assert [(e.kind, e.text) for e in events[:-1]] == [("reasoning", "Думаю."), ("content", "Ответ")]
    end = events[-1]
    assert end.raw == {"reasoning_details": details} and end.usage["cost_microusd"] == 123
    run(router, [{"role": "user", "content": "привет"},
                 {"role": "assistant", "content": "Ответ", "provider": "openrouter", "provider_raw": end.raw},
                 {"role": "user", "content": "ещё"}])  # fmt: skip
    assert api.sent[1]["messages"][1] == {"role": "assistant", "content": "Ответ", "reasoning_details": details}


def test_a_tool_loop_through_the_hub_records_the_reported_cost(hub):  # noqa: F811
    details = [{"type": "reasoning.encrypted", "data": "opaque-1", "id": "r1"}]
    first = sse(
        {"choices": [{"delta": {"reasoning_details": details, "tool_calls": [
            {"index": 0, "id": "call-1", "function": {"name": "list_projects", "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 300, "completion_tokens": 10, "cost": 0.0005}},
    )  # fmt: skip
    second = sse({"choices": [{"delta": {"content": "Проектов нет."}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 400, "completion_tokens": 5, "cost": 0.0007}})  # fmt: skip
    answers = iter([first, second])
    api = Api(ROUTER_MODELS)

    def handler(request):
        if not request.url.path.endswith("/models"):
            api.answer = next(answers)
        return api(request)

    router = OpenRouterProvider(lambda: KEY, chosen=lambda: _async(["qwen/qwen3.8-coder"]),
                                transport=httpx.MockTransport(handler))  # fmt: skip
    settled(hub)  # the hub's own first sync (its keyless provider of this name) must not come after ours
    hub.app.state.gateway.register(router)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    sent = hub.post(
        f"/v1/conversations/{conv}/messages",
        json={"content": "сколько проектов?", "model_id": "openrouter/qwen/qwen3.8-coder"},
    )
    assert sent.status_code == 202, sent.text
    messages = finished(hub, conv)
    assert messages[-1]["content"][-1]["text"] == "Проектов нет."
    replayed = api.sent[1]["messages"]
    assert replayed[1]["reasoning_details"] == details and replayed[1]["tool_calls"][0]["id"] == "call-1"
    assert hub.get("/v1/usage").json()["total_cost_usd"] == pytest.approx(0.0012)  # what OpenRouter charged


def test_efforts_and_reasoning_pieces():
    assert nearest_effort("xhigh", ["low", "medium", "high"]) == "high"
    assert nearest_effort("low", ["medium", "high"]) == "medium"
    assert nearest_effort("max", []) == "max"  # unknown: as asked
    pieces = [{"type": "reasoning.text", "index": 0, "text": "Сна", "format": "x"},
              {"type": "reasoning.text", "index": 0, "text": "чала", "signature": "sig"},
              {"type": "reasoning.encrypted", "index": 1, "data": "AAA"}]  # fmt: skip
    assert join_details(pieces) == [
        {"type": "reasoning.text", "index": 0, "text": "Сначала", "format": "x", "signature": "sig"},
        {"type": "reasoning.encrypted", "index": 1, "data": "AAA"},
    ]


def test_openrouter_takes_each_models_efforts():
    models = [dict(ROUTER_MODELS[0], reasoning={"supported_efforts": ["low", "medium", "high"]}),
              {"id": "plain/model", "name": "Plain", "pricing": {"prompt": "0.000001", "completion": "0.000001"},
               "supported_parameters": ["tools"]}]  # fmt: skip
    api = Api(models, sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}))
    router = OpenRouterProvider(lambda: KEY, chosen=lambda: _async([]), transport=api.transport())
    asyncio.run(router.everything())
    run(router, [{"role": "user", "content": "x"}], effort="max")
    assert api.sent[0]["reasoning"] == {"effort": "high"}  # its strongest
    router_plain = OpenRouterProvider(lambda: KEY, chosen=lambda: _async([]), transport=api.transport())
    asyncio.run(router_plain.everything())

    async def go():
        return [e async for e in router_plain.stream_chat("plain/model", [{"role": "user", "content": "x"}], "high",
                                                           asyncio.Event())]  # fmt: skip

    asyncio.run(go())
    assert "reasoning" not in api.sent[1]  # a model without a reasoning setting gets none


def settled(hub):  # noqa: F811
    """Waits for the hub's startup catalog sync."""

    async def wait():
        await hub.app.state.initial_sync

    hub.portal.call(wait)
