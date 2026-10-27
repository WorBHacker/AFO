"""The Gemini adapter (stage 6.5): the catalog by key with prices, the request as Gemini takes it, the streamed
answer with thinking, calls and usage, and Gemini's own turns replayed with their thought signatures unchanged.
The Gemini API is a mock: nothing leaves this machine."""

import asyncio
import json
from datetime import date

import httpx
import pytest

from hub.gateway.base import ProviderError
from hub.gateway.gemini_provider import NO_ID, PLACEHOLDER_SIGNATURE, GeminiProvider, prices_on, to_wire
from tests.test_changes import finished
from tests.test_tools import hub  # noqa: F401 - the hub fixture

KEY = "AIza-test-key-123456"
MODELS = {
    "models": [
        {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576,
         "outputTokenLimit": 65536, "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash", "inputTokenLimit": 1048576,
         "outputTokenLimit": 65536, "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-9-unknown", "supportedGenerationMethods": ["generateContent"]},
    ]
}  # fmt: skip


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks)


class Api:
    """The parts of the Gemini API the adapter uses; it keeps what it was sent."""

    def __init__(self, answer="", status=200):
        self.answer = answer
        self.status = status
        self.sent = []

    def __call__(self, request):
        assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        self.sent.append(json.loads(request.content))
        assert request.url.params["alt"] == "sse"
        if self.status != 200:
            return httpx.Response(
                self.status,
                json={
                    "error": {
                        "code": self.status,
                        "message": "API key not valid.",
                        "details": [{"reason": "API_KEY_INVALID"}],
                    }
                },
            )
        return httpx.Response(200, text=self.answer, headers={"content-type": "text/event-stream"})


def provider(api, key=KEY, today=date(2026, 10, 27)):
    return GeminiProvider(lambda: key, transport=httpx.MockTransport(api), today=lambda: today)


def collect(gen):
    async def run():
        return [e async for e in gen]

    return asyncio.run(run())


def test_the_catalog_is_the_priced_models_the_key_can_use():
    gemini = provider(Api())
    models = asyncio.run(gemini.list_models())
    assert [m.id for m in models] == ["gemini/gemini-3.8-flash", "gemini/gemini-2.5-flash"]
    flash = models[0]
    assert flash.locality == "cloud" and flash.prices == {"input": 0.75, "output": 3.75, "cache_read": 0.075}
    assert flash.capabilities["context"] == 1048576 and flash.capabilities["prices_asof"]
    assert asyncio.run(GeminiProvider(lambda: None).list_models()) == []  # no key: no models, no network
    later = asyncio.run(provider(Api(), today=date(2027, 1, 2)).list_models())[0]
    assert later.prices["input"] == 1.50  # the rise Google announced for 2027


def test_prices_follow_their_dates():
    schedule = [("2026-01-01", {"input": 1.0}), ("2026-12-01", {"input": 2.0})]
    assert prices_on(schedule, date(2026, 11, 30)) == {"input": 1.0}
    assert prices_on(schedule, date(2026, 12, 1)) == {"input": 2.0}


def test_a_streamed_answer_with_thinking_a_call_and_usage():
    answer = sse(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Думаю", "thought": True}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": " дальше", "thought": True}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "Посмотрю файл."}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"id": "fc-1", "name": "read_file", "args": {"path": "a.py"}}, "thoughtSignature": "SIG1"},
            {"functionCall": {"name": "list_projects", "args": {}}}]}, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 1200, "cachedContentTokenCount": 1000, "candidatesTokenCount": 40,
                           "thoughtsTokenCount": 60}},
    )  # fmt: skip
    api = Api(answer)
    gemini = provider(api)
    asyncio.run(gemini.list_models())  # learns each model's output limit
    tools = [{"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {}}}]
    messages = [{"role": "system", "content": "Ты помощник."}, {"role": "user", "content": "что в a.py?"}]
    events = collect(gemini.stream_chat("gemini-3.8-flash", messages, "xhigh", asyncio.Event(), tools=tools))

    sent = api.sent[0]
    assert sent["systemInstruction"] == {"parts": [{"text": "Ты помощник."}]}
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "что в a.py?"}]}]
    assert sent["generationConfig"] == {"thinkingConfig": {"includeThoughts": True, "thinkingLevel": "high"},
                                        "maxOutputTokens": 65536}  # fmt: skip
    assert sent["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == tools[0]["parameters"]
    assert [(e.kind, e.text) for e in events[:-1]] == [("reasoning", "Думаю"), ("reasoning", " дальше"),
                                                       ("content", "Посмотрю файл.")]  # fmt: skip
    end = events[-1]
    assert end.usage == {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 100}
    assert end.tool_calls == [{"id": "fc-1", "name": "read_file", "input": {"path": "a.py"}},
                              {"id": f"{NO_ID}1", "name": "list_projects", "input": {}}]  # fmt: skip
    assert end.raw["content"]["parts"] == [
        {"text": "Думаю дальше", "thought": True},  # streamed pieces joined
        {"text": "Посмотрю файл."},
        {"functionCall": {"id": "fc-1", "name": "read_file", "args": {"path": "a.py"}}, "thoughtSignature": "SIG1"},
        {"functionCall": {"name": "list_projects", "args": {}}},
    ]
    assert end.refusal is None and end.stop_reason == "end_turn"


def test_a_2_5_model_gets_a_thinking_budget():
    api = Api(sse({"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}))
    collect(provider(api).stream_chat("gemini-2.5-flash", [{"role": "user", "content": "hi"}], "low", asyncio.Event()))
    assert api.sent[0]["generationConfig"]["thinkingConfig"] == {"includeThoughts": True, "thinkingBudget": 1024}


def test_signed_parts_are_never_joined():
    answer = sse(
        {"candidates": [{"content": {"parts": [{"text": "Ответ"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "", "thoughtSignature": "SIG"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": " потом"}]}}], "usageMetadata": {"candidatesTokenCount": 3}},
    )
    end = collect(provider(Api(answer)).stream_chat("gemini-3.8-flash", [{"role": "user", "content": "x"}], "low",
                                                    asyncio.Event()))[-1]  # fmt: skip
    assert end.raw["content"]["parts"] == [{"text": "Ответ"}, {"text": "", "thoughtSignature": "SIG"},
                                           {"text": " потом"}]  # fmt: skip


def test_history_on_the_wire():
    own = {"content": {"role": "model", "parts": [
        {"text": "мысль", "thought": True},
        {"functionCall": {"id": "fc-1", "name": "read_file", "args": {"path": "a.py"}}, "thoughtSignature": "SIG1"},
        {"functionCall": {"name": "list_projects", "args": {}}}]}}  # fmt: skip
    messages = [
        {"role": "system", "content": "Роль."},
        {"role": "user", "content": "что в a.py?"},
        {"role": "system", "content": "Projects: LocalChat"},
        {"role": "assistant", "content": "", "provider": "gemini", "provider_raw": own,
         "tool_calls": [{"id": "fc-1", "name": "read_file", "input": {}}]},
        {"role": "tool", "tool_call_id": "fc-1", "name": "read_file", "content": "print(1)", "is_error": False},
        {"role": "tool", "tool_call_id": f"{NO_ID}1", "name": "list_projects", "content": "нет", "is_error": True},
        {"role": "assistant", "content": "Готово.", "provider": "anthropic", "provider_raw": {"content": []}},
        {"role": "user", "content": "а теперь?"},
        {"role": "assistant", "content": "Смотрю.", "tool_calls": [{"id": "t1", "name": "git_status", "input": {}},
                                                                   {"id": "t2", "name": "read_file", "input": {}}]},
    ]  # fmt: skip
    system, contents = to_wire(messages, "gemini")
    assert system == "Роль."
    assert contents[0] == {
        "role": "user",
        "parts": [{"text": "что в a.py?"}, {"text": "<context>\nProjects: LocalChat\n</context>"}],
    }
    assert contents[1] == {"role": "model", "parts": own["content"]["parts"]}  # replayed exactly as received
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "read_file", "response": {"output": "print(1)"}, "id": "fc-1"}},
            {"functionResponse": {"name": "list_projects", "response": {"error": "нет"}}},
        ],
    }  # no id it never sent
    assert contents[3] == {"role": "model", "parts": [{"text": "Готово."}]}  # another provider's: its text
    assert contents[5]["parts"][1] == {"functionCall": {"id": "t1", "name": "git_status", "args": {}},
                                       "thoughtSignature": PLACEHOLDER_SIGNATURE}  # fmt: skip
    assert "thoughtSignature" not in contents[5]["parts"][2]


def test_refusals_and_errors():
    blocked = sse({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}, "usageMetadata": {"promptTokenCount": 9}})
    end = collect(provider(Api(blocked)).stream_chat("gemini-3.8-flash", [{"role": "user", "content": "x"}], "low",
                                                     asyncio.Event()))[-1]  # fmt: skip
    assert "PROHIBITED_CONTENT" in end.refusal and end.usage["input_tokens"] == 9  # its tokens still count
    with pytest.raises(ProviderError, match="the API key was rejected"):
        collect(provider(Api(status=400)).stream_chat("gemini-3.8-flash", [{"role": "user", "content": "x"}], "low",
                                                      asyncio.Event()))  # fmt: skip
    with pytest.raises(ProviderError, match="no Gemini API key"):
        collect(GeminiProvider(lambda: None).stream_chat("gemini-3.8-flash", [], "low", asyncio.Event()))


def test_a_tool_loop_through_the_hub_replays_the_signed_turn(hub, repo):  # noqa: F811
    first = sse({"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"id": "fc-9", "name": "list_projects", "args": {}}, "thoughtSignature": "SIG9"}]},
        "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 5}})  # fmt: skip
    second = sse({"candidates": [{"content": {"role": "model", "parts": [{"text": "Один проект."}]},
                                  "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 80,
                                                                              "candidatesTokenCount": 4}})  # fmt: skip
    api = Api()
    answers = iter([first, second])

    def handler(request):
        if not request.url.path.endswith("/models"):
            api.answer = next(answers)
        return api(request)

    hub.app.state.gateway.register(GeminiProvider(lambda: KEY, transport=httpx.MockTransport(handler)))
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    sent = hub.post(
        f"/v1/conversations/{conv}/messages",
        json={"content": "сколько проектов?", "model_id": "gemini/gemini-3.8-flash"},
    )
    assert sent.status_code == 202
    messages = finished(hub, conv)
    assert messages[-1]["content"][-1]["text"] == "Один проект."
    replayed = api.sent[1]["contents"]
    assert replayed[1] == {
        "role": "model",
        "parts": [{"functionCall": {"id": "fc-9", "name": "list_projects", "args": {}}, "thoughtSignature": "SIG9"}],
    }
    assert replayed[2]["parts"][0]["functionResponse"]["id"] == "fc-9"
    usage = hub.get("/v1/usage").json()
    assert usage["total_cost_usd"] > 0  # priced: 130 input and 9 output tokens of Gemini 3.8 Flash
