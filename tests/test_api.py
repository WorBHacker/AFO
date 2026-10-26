"""The hub's HTTP and WebSocket API through Starlette's TestClient (no real network)."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from common.paths import data_paths
from hub.auth import create_client, revoke_client
from hub.security import hostname_of
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def hub():
    # no status monitor, and the startup catalog sync finished: the WebSocket tests see only their own events
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0))
    with TestClient(app, base_url=BASE) as client:
        client.app_token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")

        async def initial_sync_done():
            await app.state.initial_sync

        client.portal.call(initial_sync_done)
        yield client


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- HTTP ---


def test_health_needs_no_token(hub):
    body = hub.get("/v1/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "localchat-hub"


def test_info_needs_a_valid_token(hub):
    assert hub.get("/v1/info").status_code == 401
    assert hub.get("/v1/info", headers=auth("wrong")).status_code == 401
    body = hub.get("/v1/info", headers=auth(hub.app_token)).json()
    assert body["client"] == {"name": "app", "cloud": False}
    assert body["schema_version"] >= 1
    assert body["last_event_id"] >= 1  # hub.started


def test_other_clients_count_as_cloud_and_can_be_revoked(hub):
    db = hub.app.state.db
    token = db.call(lambda c: create_client(c, "cline"))
    assert hub.get("/v1/info", headers=auth(token)).json()["client"] == {"name": "cline", "cloud": True}
    db.call(lambda c: revoke_client(c, "cline"))
    assert hub.get("/v1/info", headers=auth(token)).status_code == 401


def test_shutdown_only_for_the_app(hub):
    calls = []
    assert hub.post("/v1/shutdown", headers=auth(hub.app_token)).status_code == 503  # not started by `python -m hub`
    hub.app.state.request_shutdown = lambda: calls.append(True)
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.post("/v1/shutdown", headers=auth(cline)).status_code == 403
    assert hub.post("/v1/shutdown").status_code == 401
    assert calls == []
    assert hub.post("/v1/shutdown", headers=auth(hub.app_token)).status_code == 202
    assert calls == [True]


def test_foreign_host_is_refused(hub):
    response = hub.get("/v1/health", headers={"Host": "evil.example:8765"})
    assert response.status_code == 403
    assert "not this computer" in response.json()["detail"]


def test_web_page_origin_is_refused(hub):
    response = hub.get("/v1/info", headers={**auth(hub.app_token), "Origin": "https://evil.example"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1:8765", "127.0.0.1"),
        ("LocalHost", "localhost"),
        ("[::1]:8765", "::1"),
        ("evil.example", "evil.example"),
    ],
)
def test_hostname_of(host, expected):
    assert hostname_of(host) == expected


# --- WebSocket ---


# Starlette's test client sends `Host: testserver` on WebSockets whatever base_url says, and the hub
# refuses that host, so the WebSocket tests name the real one.
WS_HOST = {"Host": "127.0.0.1:8765"}


def ws_headers(token=None, **extra):
    return {**WS_HOST, **(auth(token) if token else {}), **extra}


def test_ws_with_test_client_host_is_refused(hub):
    with pytest.raises(WebSocketDisconnect) as exc, hub.websocket_connect("/v1/events", headers=auth(hub.app_token)):
        pass
    assert exc.value.code == 1008


def test_ws_without_token_is_refused(hub):
    with pytest.raises(WebSocketDisconnect) as exc, hub.websocket_connect("/v1/events", headers=ws_headers()) as ws:
        ws.receive_json()
    assert exc.value.code == 1008


def test_ws_from_web_page_is_refused(hub):
    headers = ws_headers(hub.app_token, Origin="https://evil.example")
    with pytest.raises(WebSocketDisconnect) as exc, hub.websocket_connect("/v1/events", headers=headers) as ws:
        ws.receive_json()
    assert exc.value.code == 1008


def test_ws_hello_then_live_events(hub):
    with hub.websocket_connect("/v1/events", headers=ws_headers(hub.app_token)) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hub.hello"
        assert hello["payload"]["client"] == "app"
        hub.portal.call(hub.app.state.bus.publish, "test.ping", {"текст": "привет"})
        event = ws.receive_json()
        assert event["type"] == "test.ping"
        assert event["payload"] == {"текст": "привет"}
        assert event["id"] > hello["payload"]["last_event_id"]


def test_ws_subscribe_filters_and_replays_missed_events(hub):
    bus = hub.app.state.bus
    first = hub.portal.call(bus.publish, "a.one", {})
    hub.portal.call(bus.publish, "b.one", {})
    with hub.websocket_connect("/v1/events", headers=ws_headers(hub.app_token)) as ws:
        ws.receive_json()  # hello
        ws.send_json({"op": "subscribe", "topics": ["a.*"], "since": first.id - 1})
        replayed = ws.receive_json()
        assert (replayed["type"], replayed["id"]) == ("a.one", first.id)
        subscribed = ws.receive_json()
        assert subscribed["type"] == "hub.subscribed"
        assert subscribed["payload"]["replayed_up_to"] == first.id
        hub.portal.call(bus.publish, "b.two", {})
        hub.portal.call(bus.publish, "a.two", {})
        assert ws.receive_json()["type"] == "a.two"  # b.two was filtered out


def test_ws_ping_and_bad_frames(hub):
    with hub.websocket_connect("/v1/events", headers=ws_headers(hub.app_token)) as ws:
        ws.receive_json()
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"op": "dance"})
        assert "unknown op" in ws.receive_json()["payload"]["detail"]
