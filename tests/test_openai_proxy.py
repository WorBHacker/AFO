"""The hub as an OpenAI-compatible server (stage 6.3, D33): models, answers whole and streamed, tool calls both ways,
usage recorded for the client, budgets, errors in OpenAI's shape."""

import json

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.auth import create_client
from hub.gateway.base import Model, StreamEvent
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"


class Scripted:
    """A provider that answers as scripted and remembers what it was asked."""

    name = "fake"

    def __init__(self, events, prices=None):
        self.events = events
        self.asked = []
        self.models = [Model("fake/echo", "fake", "echo", "Echo", "cloud" if prices else "local", {}, prices or {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.asked.append({"messages": messages, "effort": effort, "tools": tools})
        for event in self.events:
            yield event


ANSWER = [
    StreamEvent("reasoning", "думаю"),
    StreamEvent("content", "При"),
    StreamEvent("content", "вет"),
    StreamEvent("end", usage={"input_tokens": 12, "output_tokens": 3, "cached_input_tokens": 4},
                stats={"tok_s": 40.0}, stop_reason="end_turn"),
]  # fmt: skip
TOOL_CALL = [StreamEvent("end", usage={"input_tokens": 20, "output_tokens": 8}, stop_reason="tool_use",
                         tool_calls=[{"id": "t1", "name": "get_weather", "input": {"city": "Ташкент"}}])]  # fmt: skip


@pytest.fixture
def hub():
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(app, base_url=BASE) as client:
        token = app.state.db.call(lambda c: create_client(c, "cline", cloud=False))
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def use(hub, provider):
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    return provider


def test_models_and_a_whole_answer(hub):
    provider = use(hub, Scripted(ANSWER))
    listed = hub.get("/v1/openai/models").json()
    assert {"id": "fake/echo", "object": "model", "created": 0, "owned_by": "fake"} in listed["data"]
    body = {"model": "fake/echo", "reasoning_effort": "high", "messages": [
        {"role": "system", "content": "Отвечай кратко."},
        {"role": "user", "content": [{"type": "text", "text": "Привет?"}]},
    ]}  # fmt: skip
    answer = hub.post("/v1/openai/chat/completions", json=body).json()
    choice = answer["choices"][0]
    assert answer["object"] == "chat.completion" and answer["model"] == "fake/echo"
    assert choice["message"] == {"role": "assistant", "content": "Привет", "reasoning_content": "думаю"}
    assert choice["finish_reason"] == "stop"
    assert answer["usage"] == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
                               "prompt_tokens_details": {"cached_tokens": 4}}  # fmt: skip
    assert provider.asked[0]["effort"] == "high"
    assert provider.asked[0]["messages"] == [{"role": "system", "content": "Отвечай кратко."},
                                             {"role": "user", "content": "Привет?"}]  # fmt: skip
    row = hub.app.state.db.call(lambda c: c.execute(
        "SELECT u.model_id, u.output_tokens, u.meta, c.name FROM usage u JOIN clients c ON c.id = u.client_id"
    ).fetchone())  # fmt: skip
    assert (row["model_id"], row["output_tokens"], row["name"]) == ("fake/echo", 3, "cline")
    assert json.loads(row["meta"])["proxy"] is True


def test_a_streamed_answer(hub):
    use(hub, Scripted(ANSWER))
    body = {"model": "fake/echo", "stream": True, "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "Привет?"}]}  # fmt: skip
    with hub.stream("POST", "/v1/openai/chat/completions", json=body) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line.removeprefix("data: ") for line in response.iter_lines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(line) for line in lines[:-1]]
    deltas = [c["choices"][0]["delta"] for c in chunks if c["choices"]]
    assert deltas[0] == {"role": "assistant", "content": ""}
    assert "".join(d.get("content", "") for d in deltas) == "Привет" and {"reasoning_content": "думаю"} in deltas
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["total_tokens"] == 15


WEATHER = {"type": "object", "properties": {"city": {"type": "string"}}}
USER = [{"role": "user", "content": "x"}]


def test_tool_calls_both_ways(hub):
    provider = use(hub, Scripted(TOOL_CALL))
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "Погода", "parameters": WEATHER}}]
    earlier = {
        "id": "t0",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Самарканд"}'},
    }
    history = [
        {"role": "user", "content": "Погода?"},
        {"role": "assistant", "content": None, "tool_calls": [earlier]},
        {"role": "tool", "tool_call_id": "t0", "content": "+20"},
    ]
    body = {"model": "fake/echo", "tools": tools, "messages": history}
    choice = hub.post("/v1/openai/chat/completions", json=body).json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    (call,) = choice["message"]["tool_calls"]
    assert call["id"] == "t1" and call["function"] == {"name": "get_weather", "arguments": '{"city": "Ташкент"}'}
    asked = provider.asked[0]
    assert asked["tools"] == [{"name": "get_weather", "description": "Погода", "parameters": WEATHER}]
    assert asked["messages"][1]["tool_calls"] == [{"id": "t0", "name": "get_weather", "input": {"city": "Самарканд"}}]
    tool_turn = {"role": "tool", "tool_call_id": "t0", "name": "", "content": "+20", "is_error": False}
    assert asked["messages"][2] == tool_turn


def test_errors_in_openais_shape(hub):
    use(hub, Scripted(ANSWER, prices={"input": 1.0, "output": 1.0}))
    missing = hub.post("/v1/openai/chat/completions", json={"model": "nope/x", "messages": USER})
    assert missing.status_code == 404 and missing.json()["error"]["type"] == "model_not_found"
    empty = hub.post("/v1/openai/chat/completions", json={"model": "fake/echo", "messages": []})
    assert empty.status_code == 400 and "messages" in empty.json()["error"]["message"]
    bad_tool = hub.post(
        "/v1/openai/chat/completions", json={"model": "fake/echo", "tools": [{"type": "code"}], "messages": USER}
    )
    assert bad_tool.status_code == 400
    used_up = (
        "INSERT INTO budgets (scope, scope_ref, period, hard_usd, hard_action) VALUES ('total', '', 'month', 0, 'ask')"
    )
    hub.app.state.db.call(lambda c: c.execute(used_up))
    over = hub.post("/v1/openai/chat/completions", json={"model": "fake/echo", "messages": USER})
    assert over.status_code == 402 and over.json()["error"]["type"] == "insufficient_quota"
    assert hub.get("/v1/openai/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
