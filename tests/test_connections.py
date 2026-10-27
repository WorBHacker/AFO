"""Connections (D13): keys, tokens and addresses behind the user's password. Values go in and never come out; the
window unlocks for a while with the password; wrong passwords make the hub wait; other clients see nothing.

The secret store lives in memory and the providers are fakes, so nothing leaves this machine.
"""

import json
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import connections as conns
from hub import websearch
from hub.__main__ import main
from hub.auth import create_client
from hub.db import Database, migrate
from hub.gateway.anthropic_provider import AnthropicProvider
from hub.secrets import ANTHROPIC_API_KEY, MemorySecretStore
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"
KEY = "sk-ant-test-value-123"
PASSWORD = "кот-в-сапогах"


class FakeModelsClient:
    def __init__(self):
        self.models = SimpleNamespace(list=self._list)

    async def _list(self):
        yield SimpleNamespace(id="claude-opus-5-5", display_name="Claude Opus 5.5", max_input_tokens=1_000_000)


@pytest.fixture
def hub():
    store = MemorySecretStore()
    app = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9"), secret_store=store)
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.store = store
        app.state.gateway.register(
            AnthropicProvider(lambda: store.get(ANTHROPIC_API_KEY), client_factory=lambda key: FakeModelsClient())
        )
        yield client


def unlocked(hub, password=PASSWORD):
    """The password set (the first time) and a ticket for changes."""
    if not hub.get("/v1/connections").json()["password"]:
        assert hub.post("/v1/connections/password", json={"password": password}).status_code == 200
    ticket = hub.post("/v1/connections/unlock", json={"password": password}).json()["ticket"]
    return {conns.UNLOCK_HEADER: ticket}


def row(hub, name, headers=None):
    return next(c for c in hub.get("/v1/connections", headers=headers).json()["connections"] if c["name"] == name)


def enabled_ids(hub):
    return {m["id"] for m in hub.get("/v1/models").json()["models"]}


# --- the password ---


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_password_and_the_unlocks():
    clock = Clock()
    lock = conns.ConnectionLock(MemorySecretStore(), clock=clock)
    with pytest.raises(conns.LockError) as none:
        lock.unlock("anything")
    assert none.value.code == "no_password" and not lock.has_password
    with pytest.raises(conns.LockError, match="не короче 6"):
        lock.set_password("12345")
    lock.set_password(PASSWORD)
    ticket = lock.unlock(PASSWORD)
    lock.check(ticket)
    clock.now += conns.TICKET_S - 1
    lock.check(ticket)  # a use keeps it alive
    clock.now += conns.TICKET_S - 1
    assert lock.is_open(ticket)
    clock.now += 2
    with pytest.raises(conns.LockError) as lapsed:
        lock.check(ticket)  # ten minutes without a change: locked
    assert lapsed.value.code == "locked" and lapsed.value.status == 401

    with pytest.raises(conns.LockError) as wrong:
        lock.set_password("новый-пароль", old="не тот")
    assert wrong.value.code == "wrong_password"
    ticket = lock.unlock(PASSWORD)
    lock.set_password("новый-пароль", old=PASSWORD)
    assert not lock.is_open(ticket)  # a new password locks every window
    assert lock.unlock("новый-пароль")
    lock.lock()
    assert not lock.is_open(ticket)


def test_wrong_passwords_make_the_next_try_wait():
    clock = Clock()
    lock = conns.ConnectionLock(MemorySecretStore(), clock=clock)
    lock.set_password(PASSWORD)
    for _ in range(conns.FREE_TRIES):
        with pytest.raises(conns.LockError, match="неверный пароль"):
            lock.unlock("угадайка")
    with pytest.raises(conns.LockError) as waiting:
        lock.unlock(PASSWORD)  # even the right one waits
    assert waiting.value.code == "wait" and waiting.value.status == 429 and waiting.value.retry_after == 30
    clock.now += 30
    with pytest.raises(conns.LockError):
        lock.unlock("опять")
    with pytest.raises(conns.LockError) as longer:
        lock.unlock(PASSWORD)
    assert longer.value.retry_after == 60  # each wrong one after the free tries waits longer
    clock.now += 60
    assert lock.unlock(PASSWORD)  # and the right one resets the count


def test_the_hash_is_salted_and_slow():
    one, two = conns.hash_password(PASSWORD), conns.hash_password(PASSWORD)
    assert one["hash"] != two["hash"] and one["n"] == conns.SCRYPT_N
    assert conns.password_matches(one, PASSWORD) and not conns.password_matches(one, PASSWORD + "!")
    assert PASSWORD not in json.dumps(one)


# --- the API ---


def test_a_key_goes_in_and_never_comes_out(hub):
    listed = hub.get("/v1/connections").json()
    assert listed["password"] is False and listed["unlocked"] is False
    assert [c["name"] for c in listed["connections"]] == [c.name for c in conns.CONNECTIONS]
    refused = hub.put(f"/v1/connections/{ANTHROPIC_API_KEY}", json={"value": KEY})
    assert refused.status_code == 401 and refused.json()["detail"]["code"] == "locked"  # no password, no change

    headers = unlocked(hub)
    assert "anthropic/claude-opus-5-5" not in enabled_ids(hub)
    put = hub.put(f"/v1/connections/{ANTHROPIC_API_KEY}", json={"value": f"  {KEY}\n"}, headers=headers)
    assert put.status_code == 200 and put.json()["catalog"]["synced"] >= 1 and KEY not in put.text
    assert hub.store.get(ANTHROPIC_API_KEY) == KEY  # trimmed
    assert "anthropic/claude-opus-5-5" in enabled_ids(hub)  # its models came with it
    shown = row(hub, ANTHROPIC_API_KEY, headers)
    assert shown["set"] is True and shown["tail"] == "-123"  # unlocked: the last four characters, to recognise it
    assert row(hub, ANTHROPIC_API_KEY)["tail"] == ""  # locked: not even the last characters
    events = hub.app.state.db.call(lambda c: [r[0] for r in c.execute("SELECT payload FROM events").fetchall()])
    assert not [p for p in events if KEY in p or PASSWORD in p]

    assert hub.delete(f"/v1/connections/{ANTHROPIC_API_KEY}", headers=headers).json()["set"] is False
    assert "anthropic/claude-opus-5-5" not in enabled_ids(hub)
    hub.post("/v1/connections/lock", headers=headers)
    assert hub.put(f"/v1/connections/{ANTHROPIC_API_KEY}", json={"value": KEY}, headers=headers).status_code == 401


def test_values_are_checked(hub):
    headers = unlocked(hub)
    bad = hub.put(f"/v1/connections/{conns.TELEGRAM_TOKEN}", json={"value": "not-a-token"}, headers=headers)
    assert bad.status_code == 422 and "@BotFather" in bad.json()["detail"]
    assert hub.put("/v1/connections/ssh_password", json={"value": "x" * 10}, headers=headers).status_code == 404
    assert hub.put(f"/v1/connections/{conns.SEARXNG_URL}", json={"value": "127.0.0.1"}, headers=headers).status_code \
        == 422  # fmt: skip
    ok = hub.put(f"/v1/connections/{conns.SEARXNG_URL}", json={"value": "http://127.0.0.1:8888/"}, headers=headers)
    assert ok.status_code == 200
    assert row(hub, conns.SEARXNG_URL)["value"] == "http://127.0.0.1:8888"  # an address is no secret: shown
    assert hub.store.get(conns.SEARXNG_URL) is None  # and kept in the settings, not the Credential Manager


def test_wrong_passwords_reach_the_feed_and_other_clients_see_nothing(hub):
    unlocked(hub)
    wrong = hub.post("/v1/connections/unlock", json={"password": "не тот"})
    assert wrong.status_code == 403 and wrong.json()["detail"]["code"] == "wrong_password"
    feed = hub.get("/v1/feed", params={"types": "connections.denied,connections.password"}).json()["events"]
    assert [e["type"] for e in feed] == ["connections.denied", "connections.password"]
    assert "не тот" not in json.dumps(feed)
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    other = {"Authorization": f"Bearer {cline}"}
    assert hub.get("/v1/connections", headers=other).status_code == 403
    assert hub.post("/v1/connections/unlock", json={"password": PASSWORD}, headers=other).status_code == 403
    assert hub.post("/v1/connections/password", json={"password": "захват!"}, headers=other).status_code == 403


def test_checking_a_connection(hub, monkeypatch):
    headers = unlocked(hub)
    hub.put(f"/v1/connections/{ANTHROPIC_API_KEY}", json={"value": KEY}, headers=headers)
    checked = hub.post(f"/v1/connections/{ANTHROPIC_API_KEY}/check", headers=headers).json()
    assert checked == {"name": ANTHROPIC_API_KEY, "ok": True, "detail": "ключ работает: моделей — 1"}

    def brave(request):
        assert request.headers["X-Subscription-Token"] == "BSA-test-key-1"
        return httpx.Response(200, json={"web": {"results": [{"title": "LocalChat", "url": "https://x.example"}]}})

    monkeypatch.setattr(websearch, "TRANSPORT", httpx.MockTransport(brave))
    hub.put(f"/v1/connections/{conns.BRAVE_SEARCH_KEY}", json={"value": "BSA-test-key-1"}, headers=headers)
    assert hub.post(f"/v1/connections/{conns.BRAVE_SEARCH_KEY}/check", headers=headers).json()["detail"] == \
        "поиск работает: результатов — 1"  # fmt: skip

    def telegram(request):
        assert request.url.path.endswith("/getMe")
        return httpx.Response(200, json={"ok": True, "result": {"username": "my_localchat_bot"}})

    hub.app.state.telegram._http = httpx.AsyncClient(transport=httpx.MockTransport(telegram))
    hub.put(f"/v1/connections/{conns.TELEGRAM_TOKEN}", json={"value": "123456789:" + "A" * 35}, headers=headers)
    assert hub.post(f"/v1/connections/{conns.TELEGRAM_TOKEN}/check", headers=headers).json() == {
        "name": conns.TELEGRAM_TOKEN, "ok": True, "detail": "бот @my_localchat_bot на связи"}  # fmt: skip
    gemini = hub.post(f"/v1/connections/{conns.GEMINI_API_KEY}/check", headers=headers).json()
    assert gemini == {"name": conns.GEMINI_API_KEY, "ok": False, "detail": "не задан: проверять нечего"}
    assert hub.post(f"/v1/connections/{ANTHROPIC_API_KEY}/check").status_code == 401  # locked


def test_linking_the_telegram_chat_needs_the_password(hub):
    assert hub.post("/v1/connections/telegram/link").status_code == 401
    headers = unlocked(hub)
    link = hub.post("/v1/connections/telegram/link", headers=headers).json()
    assert len(link["code"]) == 6 and link["code"].isdigit() and link["valid_s"] == 600
    assert hub.get("/v1/connections").json()["telegram"] == {"linked": False}
    assert hub.delete("/v1/connections/telegram/link", headers=headers).json() == {"linked": False}


def test_a_forgotten_password_takes_the_connections_with_it(tmp_path):
    store = MemorySecretStore({ANTHROPIC_API_KEY: KEY, conns.TELEGRAM_TOKEN: "1:x"})
    conns.ConnectionLock(store).set_password(PASSWORD)
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    db.call(lambda conn: conns.set_address(conn, conns.SEARXNG_URL, "http://127.0.0.1:8888"))
    db.call(lambda conn: conn.execute("INSERT INTO mcp_servers (name, transport, command) VALUES ('gh', 'stdio', 'x')"))
    store.set("mcp_env.gh", '{"GITHUB_TOKEN": "t"}')  # an MCP server's keys, set under the password too (9.5)
    removed = db.call(lambda conn: conns.reset(store, conn))
    assert removed == [ANTHROPIC_API_KEY, conns.TELEGRAM_TOKEN, conns.SEARXNG_URL, "mcp_env.gh"]
    assert store.get(conns.PASSWORD) is None and store.get(ANTHROPIC_API_KEY) is None
    assert db.call(lambda conn: conns.address(conn, conns.SEARXNG_URL)) == ""
    db.close()


def test_the_console(capsys):
    assert main(["key", "status"]) == 0
    out = capsys.readouterr().out
    assert "anthropic_api_key" in out and "telegram_bot_token" in out and "searxng_url" not in out
    assert main(["key", "set", "anthropic_api_key"]) == 1  # a memory store would lose the key at exit
    assert main(["key", "set", "nope"]) == 1
    assert main(["connections", "reset"]) == 1  # the same: not without the Windows store


class Chooser:
    """A provider with too many models to list, like OpenRouter."""

    name = "openrouter"

    def __init__(self):
        self.everything_calls = 0

    async def everything(self):
        self.everything_calls += 1
        return [
            {
                "id": "deepseek/deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "input": 0.66,
                "output": 1.98,
                "context": 1_000_000,
            },
            {"id": "qwen/qwen3.8-coder", "name": "Qwen3.8 Coder", "input": 0.2, "output": 0.8, "context": 262_144},
        ]

    async def list_models(self):
        return []


def test_choosing_a_providers_models(hub):
    hub.app.state.gateway.register(Chooser())
    listed = hub.get(f"/v1/connections/{conns.OPENROUTER_API_KEY}/models").json()
    assert [m["id"] for m in listed["models"]] == ["deepseek/deepseek-v4-pro", "qwen/qwen3.8-coder"]
    assert listed["chosen"] == [] and not any(m["chosen"] for m in listed["models"])
    ids = ["qwen/qwen3.8-coder", " deepseek/deepseek-v4-pro ", "qwen/qwen3.8-coder"]
    assert hub.put(f"/v1/connections/{conns.OPENROUTER_API_KEY}/models", json={"ids": ids}).status_code == 401
    headers = unlocked(hub)
    saved = hub.put(f"/v1/connections/{conns.OPENROUTER_API_KEY}/models", json={"ids": ids}, headers=headers).json()
    assert saved["chosen"] == ["deepseek/deepseek-v4-pro", "qwen/qwen3.8-coder"] and "catalog" in saved
    assert row(hub, conns.OPENROUTER_API_KEY)["chosen"] == 2
    assert hub.get(f"/v1/connections/{ANTHROPIC_API_KEY}/models").status_code == 404  # its models are not chosen
