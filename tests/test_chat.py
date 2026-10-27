"""Chat through the hub end to end: a run streams over the WebSocket and is saved to the database."""

import asyncio

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.gateway.base import Model, StreamEvent
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"
WS_HOST = {"Host": "127.0.0.1:8765"}


class FakeProvider:
    """A provider that replays a fixed script and then ends with usage (no network)."""

    name = "fake"

    def __init__(self, script: list[StreamEvent], block: bool = False, end: StreamEvent | None = None, models=None):
        self.script = script
        self.block = block  # after the script, wait for cancel instead of ending (for the cancel test)
        self.end = end or StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 3}, stats={"tok_s": 50.0})
        self.models = models if models is not None else [Model("fake/echo", "fake", "echo", "Echo", "local", {}, {})]

    async def list_models(self) -> list[Model]:
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.last_prompt = messages
        for event in self.script:
            if cancel.is_set():
                break
            yield event
            await asyncio.sleep(0)  # a point where cancel can take effect
        else:
            if self.block:
                await cancel.wait()  # park until the caller cancels: makes the cancel test deterministic
        yield self.end


def default_script() -> list[StreamEvent]:
    return [
        StreamEvent("reasoning", "дай подумаю"),
        StreamEvent("content", "Привет"),
        StreamEvent("content", ", мир"),
    ]


@pytest.fixture
def hub():
    # a dead llama-swap url so the real provider's startup sync fails fast and never reaches port 8080
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9"))
    with TestClient(app, base_url=BASE) as client:
        client.token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


def auth(client):
    return {"Authorization": f"Bearer {client.token}"}


def register_fake(hub, script=None, block=False, end=None, models=None) -> FakeProvider:
    provider = FakeProvider(script or default_script(), block=block, end=end, models=models)
    hub.app.state.gateway.register(provider)
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    return provider


def drain_until(ws, event_type, limit=200):
    collected = []
    for _ in range(limit):
        message = ws.receive_json()
        collected.append(message)
        if message.get("type") == event_type:
            return collected
        if message.get("type") == "chat.error":  # the run is over: waiting on would block forever
            raise AssertionError(f"chat.error while waiting for {event_type}: {message['payload'].get('detail')}")
    raise AssertionError(f"did not see {event_type}; saw {[m.get('type') for m in collected]}")


def test_models_catalog_and_sync(hub):
    provider = register_fake(hub)
    assert provider.name in hub.get("/v1/models", headers=auth(hub)).json()["providers"]
    body = hub.get("/v1/models", headers=auth(hub)).json()
    ids = {m["id"] for m in body["models"]}
    assert "fake/echo" in ids
    echo = next(m for m in body["models"] if m["id"] == "fake/echo")
    assert echo["locality"] == "local"


def test_send_message_streams_and_saves(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={"title": "t"}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()  # hub.hello
        posted = hub.post(
            f"/v1/conversations/{conv}/messages",
            headers=auth(hub),
            json={"content": "привет", "model_id": "fake/echo"},
        )
        assert posted.status_code == 202
        assert posted.json()["user_message"]["role"] == "user"

        events = drain_until(ws, "chat.done")
        by_type = [e["type"] for e in events]
        assert "chat.delta" in by_type
        assert by_type.count("chat.message") == 2  # user, then assistant
        done = events[-1]
        assert done["payload"]["cancelled"] is False
        assert done["payload"]["cost_usd"] == 0.0  # local model
        assert done["payload"]["usage"] == {"input_tokens": 5, "output_tokens": 3}

    saved = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()["messages"]
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assistant = saved[1]
    assert assistant["model_id"] == "fake/echo"
    assert {b["type"] for b in assistant["content"]} == {"reasoning", "text"}
    text = next(b["text"] for b in assistant["content"] if b["type"] == "text")
    assert text == "Привет, мир"


def test_reasoning_is_not_sent_back_to_the_model(hub):
    provider = register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(
            f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "раз", "model_id": "fake/echo"}
        )
        drain_until(ws, "chat.done")
        hub.post(
            f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "два", "model_id": "fake/echo"}
        )
        drain_until(ws, "chat.done")
    # the second run's prompt: user, assistant (text only, no reasoning), user
    roles = [m["role"] for m in provider.last_prompt]
    assert roles == ["user", "assistant", "user"]
    assert all("думаю" not in m["content"] for m in provider.last_prompt)


def test_unknown_model_is_rejected(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    r = hub.post(f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "hi", "model_id": "nope/x"})
    assert r.status_code == 409


def test_bad_effort_and_missing_conversation(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    assert (
        hub.post(
            f"/v1/conversations/{conv}/messages",
            headers=auth(hub),
            json={"content": "hi", "model_id": "fake/echo", "effort": "turbo"},
        ).status_code
        == 422
    )
    assert (
        hub.post(
            "/v1/conversations/ghost/messages", headers=auth(hub), json={"content": "hi", "model_id": "fake/echo"}
        ).status_code
        == 404
    )


def test_cancel_saves_partial_answer(hub):
    # block=True: after two deltas the stream parks until cancel, so the cancel deterministically lands
    # mid-run rather than racing a fast stream to its natural end
    register_fake(hub, [StreamEvent("content", "часть 1 "), StreamEvent("content", "часть 2 ")], block=True)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(
            f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "go", "model_id": "fake/echo"}
        )
        for _ in range(200):
            if ws.receive_json().get("type") == "chat.delta":
                break
        assert hub.post(f"/v1/conversations/{conv}/cancel", headers=auth(hub)).json()["cancelled"] is True
        done = drain_until(ws, "chat.done")[-1]
        assert done["payload"]["cancelled"] is True
    saved = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()["messages"]
    assert saved[-1]["role"] == "assistant"  # the partial answer is kept
    text = next(b["text"] for b in saved[-1]["content"] if b["type"] == "text")
    assert text == "часть 1 часть 2 "


def test_delete_conversation(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    assert hub.delete(f"/v1/conversations/{conv}", headers=auth(hub)).status_code == 204
    assert hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).status_code == 404
    assert hub.delete(f"/v1/conversations/{conv}", headers=auth(hub)).status_code == 404


def test_usage_endpoint_counts_the_run(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        hub.post(
            f"/v1/conversations/{conv}/messages", headers=auth(hub), json={"content": "hi", "model_id": "fake/echo"}
        )
        drain_until(ws, "chat.done")
    totals = hub.get("/v1/usage", headers=auth(hub)).json()
    assert totals["requests"] == 1
    assert totals["total_output_tokens"] == 3
    assert totals["total_cost_usd"] == 0.0


def send(hub, conv, text, model_id="fake/echo"):
    body = {"content": text, "model_id": model_id}
    return hub.post(f"/v1/conversations/{conv}/messages", headers=auth(hub), json=body)


def test_provider_raw_is_stored_and_replayed_to_its_provider(hub):
    raw = {"content": [{"type": "thinking", "thinking": "t", "signature": "s"}, {"type": "text", "text": "Привет"}]}
    end = StreamEvent("end", usage={"input_tokens": 5, "output_tokens": 3}, stop_reason="end_turn", raw=raw)
    provider = register_fake(hub, end=end)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        send(hub, conv, "раз")
        assert drain_until(ws, "chat.done")[-1]["payload"]["stop_reason"] == "end_turn"
        send(hub, conv, "два")
        drain_until(ws, "chat.done")
    assistant_turn = provider.last_prompt[1]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["provider"] == "fake"
    assert assistant_turn["provider_raw"] == raw  # exactly as the provider returned it (D7, D14)
    saved = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()["messages"]
    assert "provider_raw" not in saved[1]  # clients get the hub's own blocks


def test_refusal_records_its_cost_but_not_a_message(hub):
    priced = [Model("fake/echo", "fake", "echo", "Echo", "cloud", {}, {"input": 1.0, "output": 2.0})]
    end = StreamEvent(
        "end", usage={"input_tokens": 10, "output_tokens": 4}, stop_reason="refusal", refusal="Claude declined this."
    )
    register_fake(hub, end=end, models=priced)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        send(hub, conv, "что-то")
        error = drain_until(ws, "chat.error")[-1]["payload"]
    assert error["refusal"] is True
    assert error["detail"] == "Claude declined this."
    assert error["cost_usd"] == pytest.approx((10 * 1.0 + 4 * 2.0) / 1e6)
    saved = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()["messages"]
    assert [m["role"] for m in saved] == ["user"]  # the refused turn does not join the history
    totals = hub.get("/v1/usage", headers=auth(hub)).json()
    assert (totals["requests"], totals["total_output_tokens"]) == (1, 4)  # ...but the money is counted


def test_models_a_provider_stops_listing_are_disabled(hub):
    provider = register_fake(hub)
    provider.models = []
    hub.post("/v1/models/sync", headers=auth(hub))
    ids = {m["id"] for m in hub.get("/v1/models", headers=auth(hub)).json()["models"]}
    assert "fake/echo" not in ids
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    r = send(hub, conv, "hi")
    assert r.status_code == 409
    assert "not available" in r.json()["detail"]


def test_first_message_names_the_chat_and_patch_renames_it(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    long_text = "Как   устроен\nхаб " + "очень " * 20
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        send(hub, conv, long_text)
        titled = drain_until(ws, "chat.titled")[-1]["payload"]
        drain_until(ws, "chat.done")
        send(hub, conv, "второе сообщение")
        drain_until(ws, "chat.done")
    assert titled["title"].startswith("Как устроен хаб очень")
    assert len(titled["title"]) <= 60 and titled["title"].endswith("…")
    body = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()
    assert body["title"] == titled["title"]  # the second message did not rename it
    renamed = hub.patch(f"/v1/conversations/{conv}", headers=auth(hub), json={"title": "  Хаб  "})
    assert renamed.json()["title"] == "Хаб"
    assert hub.get("/v1/conversations", headers=auth(hub)).json()["conversations"][0]["title"] == "Хаб"
    assert hub.patch("/v1/conversations/ghost", headers=auth(hub), json={"title": "x"}).status_code == 404


def test_history_carries_each_answers_usage_and_stats(hub):
    register_fake(hub)
    conv = hub.post("/v1/conversations", headers=auth(hub), json={}).json()["id"]
    with hub.websocket_connect("/v1/events", headers={**auth(hub), **WS_HOST}) as ws:
        ws.receive_json()
        send(hub, conv, "hi")
        drain_until(ws, "chat.done")
    user, assistant = hub.get(f"/v1/conversations/{conv}", headers=auth(hub)).json()["messages"]
    assert user["usage"] is None
    assert assistant["usage"]["output_tokens"] == 3
    assert assistant["usage"]["stats"]["tok_s"] == 50.0
    assert assistant["usage"]["stats"]["think_s"] >= 0  # the fake model reasoned before answering
    assert assistant["usage"]["cancelled"] is False
    totals = hub.get("/v1/usage", headers=auth(hub)).json()
    assert totals["today_requests"] == 1
