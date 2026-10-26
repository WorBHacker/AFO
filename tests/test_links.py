"""Links between projects (stage 3, D25): evidence, proposals and the user's choices, the map, a chat's context."""

import asyncio

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import context, links
from hub.auth import create_client
from hub.gateway.base import Model, StreamEvent
from hub.links import LinkError
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings


def project(root, pid, cloud_ok=True):
    return Project(id=pid, name=root.name, path=str(root), description="", cloud_ok=cloud_ok, created_at="",
                   updated_at="")  # fmt: skip


@pytest.fixture
def pair(tmp_path):
    front, back = tmp_path / "front", tmp_path / "back"
    (front / "src").mkdir(parents=True)
    back.mkdir()
    (back / "pyproject.toml").write_text('[project]\nname = "back-api"\nversion = "1"\n', encoding="utf-8")
    sources = '[tool.uv.sources]\nback-api = { path = "../back" }\n'
    (front / "pyproject.toml").write_text(
        '[project]\nname = "front"\ndependencies = ["back-api>=1"]\n' + sources, encoding="utf-8"
    )
    (front / "docker-compose.yml").write_text("services:\n  api:\n    build: ../back\n", encoding="utf-8")
    (front / "src" / "client.py").write_text("BASE = 'http://localhost:8123/api'\n", encoding="utf-8")
    (front / "README.md").write_text("Start back first: http://localhost:8123\n", encoding="utf-8")  # code wins
    return project(front, "f"), project(back, "b")


def test_evidence(pair, tmp_path):
    front, back = pair
    kinds = {}
    for p in links.scan([front, back], {"b": {8123: "python.exe"}}):
        kinds[(p["a_id"], p["b_id"], p["kind"])] = p["evidence"]
    assert set(kinds) == {("f", "b", "uses"), ("f", "b", "deploys_with")}  # nothing points from back to front
    uses = "\n".join(kinds[("f", "b", "uses")])
    assert "front/pyproject.toml:5 указывает на папку back" in uses
    assert "зависит от пакета «back-api» проекта back" in uses
    assert "front/src/client.py:1 обращается к :8123" in uses and "слушает python.exe" in uses
    assert "front/docker-compose.yml:3" in kinds[("f", "b", "deploys_with")][0]
    assert links.package_name(back) == "back-api"
    inner = tmp_path / "front" / "src"
    assert links.scan([front, project(inner, "i")], {}) == []  # a folder inside another is one tree


def test_proposals_and_choices(pair):
    from hub.db import Database, migrate

    db = Database(data_paths().ensure().db)
    db.call(migrate)
    for p in pair:
        db.call(
            lambda c, p=p: c.execute("INSERT INTO projects (id, name, path) VALUES (?, ?, ?)", (p.id, p.name, p.path))
        )
    proposals = links.scan(list(pair), {})
    assert db.call(lambda c: links.store_proposals(c, proposals)) == 2
    assert db.call(lambda c: links.store_proposals(c, proposals)) == 0  # known already
    uses = next(link for link in db.call(links.list_links) if link["kind"] == "uses")
    confirmed = db.call(lambda c: links.update_link(c, uses["id"], status="confirmed", description="REST /api :8123"))
    assert confirmed["status"] == "confirmed" and confirmed["description"] == "REST /api :8123"
    other = next(link for link in db.call(links.list_links) if link["kind"] == "deploys_with")
    db.call(lambda c: links.update_link(c, other["id"], status="rejected"))
    db.call(lambda c: links.store_proposals(c, proposals))
    assert [link["kind"] for link in db.call(links.list_links)] == ["uses"]  # rejected stays rejected, hidden
    with pytest.raises(LinkError):
        db.call(lambda c: links.update_link(c, uses["id"], kind="loves"))
    with pytest.raises(LinkError):
        db.call(lambda c: links.add_link(c, "f", "f", "uses", ""))
    manual = db.call(lambda c: links.add_link(c, "b", "f", "provides", "отдаёт API"))
    assert manual["status"] == "confirmed"
    assert db.call(lambda c: links.delete_link(c, manual["id"])) is True

    names = {"f": "front", "b": "back"}
    assert links.map_lines(db.call(links.list_links), names) == ["- front -> back: uses (REST /api :8123)"]
    local_back = [pair[0], project(pair[1].root, "b", cloud_ok=False)]
    assert "REST /api :8123" in context.map_layer(local_back, db.call(links.list_links))
    cloud_map = context.map_layer_for_cloud(local_back, db.call(links.list_links))
    assert "- front -> back: uses" in cloud_map and "REST /api" not in cloud_map
    db.close()


class Echo:
    name = "fake"

    def __init__(self):
        self.models = [Model("fake/echo", "fake", "echo", "Echo", "local", {}, {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        yield StreamEvent("content", "ок")
        yield StreamEvent("end", usage={"input_tokens": 1, "output_tokens": 1})


def test_links_api_and_a_linked_projects_state(pair):
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as hub:
        hub.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        front = hub.post("/v1/projects", json={"path": pair[0].path}).json()["id"]
        hub.post("/v1/projects", json={"path": pair[1].path})
        cline = app.state.db.call(lambda c: create_client(c, "cline"))
        assert hub.post("/v1/links/scan", headers={"Authorization": f"Bearer {cline}"}).status_code == 403
        scanned = hub.post("/v1/links/scan").json()
        assert scanned["added"] == 2 and {(x["a_name"], x["b_name"]) for x in scanned["links"]} == {("front", "back")}
        uses = next(x for x in scanned["links"] if x["kind"] == "uses")
        assert (
            hub.put(f"/v1/links/{uses['id']}", json={"status": "confirmed", "description": "REST"}).status_code == 200
        )
        assert hub.put(f"/v1/links/{uses['id']}", json={"kind": "loves"}).status_code == 422
        assert hub.put("/v1/links/999", json={"status": "confirmed"}).status_code == 404
        logged = hub.get("/v1/feed", params={"types": "link.confirmed"}).json()["events"]
        assert logged[0]["payload"] == {"a_name": "front", "b_name": "back", "kind": "uses", "description": "REST"}

        # a chat with front sees back in the map, and back's state too (D25)
        app.state.gateway.register(Echo())
        hub.portal.call(app.state.gateway.sync_catalog)
        conv = hub.post("/v1/conversations", json={}).json()["id"]
        hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [front]})
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": "раз", "model_id": "fake/echo"})
        for _ in range(300):
            messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
            if len(messages) == 3:
                break
            hub.portal.call(asyncio.sleep, 0.02)
        blocks = {b["key"]: b["text"] for b in messages[1]["content"]}
        assert "- front -> back: uses (REST)" in blocks["map"]
        linked = next(text for key, text in blocks.items() if key.startswith("linked:"))
        assert linked.startswith('<linked_project_state project="back">')
