"""API keys (D13): the API and the CLI report only whether a key is set; setting one brings Claude's models in.

The Anthropic provider here is the real one with a fake SDK client, and the secret store lives in memory,
so nothing leaves this machine and the Windows Credential Manager is never touched.
"""

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.__main__ import main
from hub.auth import create_client
from hub.gateway.anthropic_provider import AnthropicProvider
from hub.secrets import ANTHROPIC_API_KEY, MemorySecretStore, default_secret_store
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"
KEY = "sk-ant-test-value-123"


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
        client.token = (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        client.store = store
        app.state.gateway.register(
            AnthropicProvider(lambda: store.get(ANTHROPIC_API_KEY), client_factory=lambda key: FakeModelsClient())
        )
        yield client


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def enabled_ids(hub):
    return {m["id"] for m in hub.get("/v1/models", headers=auth(hub.token)).json()["models"]}


def test_status_never_shows_the_value(hub):
    body = hub.get("/v1/secrets", headers=auth(hub.token)).json()
    assert body["secrets"] == [{"name": ANTHROPIC_API_KEY, "label": "Anthropic API key (Claude)", "set": False}]
    put = hub.put(f"/v1/secrets/{ANTHROPIC_API_KEY}", headers=auth(hub.token), json={"value": f"  {KEY}\n"})
    assert put.status_code == 200
    assert KEY not in put.text
    assert hub.store.get(ANTHROPIC_API_KEY) == KEY  # stored trimmed
    listed = hub.get("/v1/secrets", headers=auth(hub.token))
    assert listed.json()["secrets"][0]["set"] is True
    assert KEY not in listed.text
    events = hub.app.state.db.call(lambda c: [r[0] for r in c.execute("SELECT payload FROM events").fetchall()])
    assert not [p for p in events if KEY in p]  # not in the event feed either


def test_a_key_brings_claude_models_and_removing_it_hides_them(hub):
    assert "anthropic/claude-opus-5-5" not in enabled_ids(hub)
    put = hub.put(f"/v1/secrets/{ANTHROPIC_API_KEY}", headers=auth(hub.token), json={"value": KEY})
    assert put.json()["catalog"]["synced"] >= 1
    assert "anthropic/claude-opus-5-5" in enabled_ids(hub)
    hub.delete(f"/v1/secrets/{ANTHROPIC_API_KEY}", headers=auth(hub.token))
    assert "anthropic/claude-opus-5-5" not in enabled_ids(hub)


def test_only_the_app_changes_secrets(hub):
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    assert hub.get("/v1/secrets", headers=auth(cline)).status_code == 200
    assert hub.put(f"/v1/secrets/{ANTHROPIC_API_KEY}", headers=auth(cline), json={"value": KEY}).status_code == 403
    assert hub.delete(f"/v1/secrets/{ANTHROPIC_API_KEY}", headers=auth(cline)).status_code == 403
    assert hub.store.get(ANTHROPIC_API_KEY) is None


def test_unknown_and_empty_secrets_are_rejected(hub):
    assert hub.put("/v1/secrets/ssh_password", headers=auth(hub.token), json={"value": "x"}).status_code == 404
    assert hub.put(f"/v1/secrets/{ANTHROPIC_API_KEY}", headers=auth(hub.token), json={"value": ""}).status_code == 422


def test_memory_store():
    store = MemorySecretStore()
    store.set("a", "1")
    assert store.get("a") == "1"
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert isinstance(default_secret_store(), MemorySecretStore)  # tests run with LOCALCHAT_SECRETS=memory


def test_cli_key_status_and_refusal_without_the_os_store(capsys):
    assert main(["key", "status"]) == 0
    assert "anthropic_api_key" in capsys.readouterr().out
    assert main(["key", "set", "anthropic_api_key"]) == 1  # a memory store would lose the key at exit
    assert main(["key", "set", "nope"]) == 1
