"""Notes for models (stage 3, D22 L4): stored per project, written by the app, given to models as a context block."""

import asyncio

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import notes
from hub.auth import create_client
from hub.gateway.base import Model, StreamEvent
from hub.limits import limit
from hub.server import create_app
from hub.settings import HubSettings


def test_the_notes_layer_keeps_to_its_budget():
    many = [{"text": f"заметка {n} " + "x" * 300} for n in range(30)]
    layer = notes.notes_layer("proj", many)
    assert layer.startswith('<notes project="proj"') and "заметка 29" in layer and "заметка 0 " not in layer
    assert "older note(s) left out to fit the budget" in layer
    assert len(layer) < limit("notes") * notes.NOTES_CHARS_PER_TOKEN + 300


class Echo:
    name = "fake"

    def __init__(self):
        self.models = [Model("fake/echo", "fake", "echo", "Echo", "local", {}, {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        yield StreamEvent("content", "ок")
        yield StreamEvent("end", usage={"input_tokens": 1, "output_tokens": 1})


@pytest.fixture
def hub():
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        yield client


def test_notes_api_and_context(hub, repo):
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    cline = hub.app.state.db.call(lambda c: create_client(c, "cline"))
    other = {"Authorization": f"Bearer {cline}"}
    assert hub.post(f"/v1/projects/{pid}/notes", json={"text": "x"}, headers=other).status_code == 403
    assert hub.post(f"/v1/projects/{pid}/notes", json={"text": "   "}).status_code == 422
    assert hub.post(f"/v1/projects/{pid}/notes", json={"text": "y" * 2000}).status_code == 422
    assert hub.post("/v1/projects/nope/notes", json={"text": "x"}).status_code == 404
    first = hub.post(f"/v1/projects/{pid}/notes", json={"text": "Деплой  только\nиз main"}).json()
    assert first["text"] == "Деплой только из main"  # one line, single spaces
    hub.post(f"/v1/projects/{pid}/notes", json={"text": "ключ в хранилище: API_KEY=sk-ant-api03-" + "n" * 40})
    listed = hub.get(f"/v1/projects/{pid}/notes", headers=other).json()["notes"]  # reading is open
    assert [n["text"][:6] for n in listed] == ["Деплой", "ключ в"]
    assert (
        hub.put(f"/v1/notes/{first['id']}", json={"text": "Деплой из main и release"})
        .json()["text"]
        .endswith("release")
    )
    assert hub.put("/v1/notes/999", json={"text": "x"}).status_code == 404

    # a chat with the project gets the notes as their own block, masked, and again only when they change
    hub.app.state.gateway.register(Echo())
    hub.portal.call(hub.app.state.gateway.sync_catalog)
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]})

    def ask(text, count):
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": text, "model_id": "fake/echo"})
        for _ in range(300):
            messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
            if len(messages) == count:
                return [m for m in messages if m["role"] == "system"][-1]["content"]
            hub.portal.call(asyncio.sleep, 0.02)
        raise AssertionError("no answer")

    block = next(b for b in ask("раз", 3) if b["key"] == f"notes:{pid}")
    assert "- Деплой из main и release" in block["text"] and "[REDACTED]" in block["text"]
    assert "sk-ant" not in block["text"]
    assert f"notes:{pid}" not in [b["key"] for b in ask("два", 6)]  # unchanged: not sent again
    assert hub.delete(f"/v1/notes/{first['id']}").status_code == 204
    assert hub.delete(f"/v1/notes/{first['id']}").status_code == 404
    assert f"notes:{pid}" in [b["key"] for b in ask("три", 9)]  # changed: sent again
