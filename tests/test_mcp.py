"""The hub's MCP server (stage 4, D32): tokens, the read tools, cloud and local clients, resources."""

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from common.paths import data_paths
from hub.auth import create_client
from hub.server import create_app
from hub.settings import HubSettings

BASE = "http://127.0.0.1:8765"
SECRET = "sk-ant-api03-" + "z" * 40
TOOLS = {
    "get_ecosystem_map", "get_project_card", "list_relations", "list_files", "read_file", "search_code",
    "find_symbol", "file_outline", "git_status", "git_diff", "git_log", "list_processes", "list_terminals",
    "read_terminal", "get_events",
}  # fmt: skip


@pytest.fixture
def hub(repo, tmp_path):
    (repo / "src" / "app.py").write_text(f"KEY = '{SECRET}'\n\ndef serve(port):\n    return port\n", encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "lib.py").write_text("class Shared:\n    pass\n", encoding="utf-8")
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url=BASE) as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        local_only = client.post("/v1/projects", json={"path": str(repo)}).json()["id"]  # local-only by default
        cloud_ok = client.post("/v1/projects", json={"path": str(shared)}).json()["id"]
        client.patch(f"/v1/projects/{cloud_ok}", json={"cloud_ok": True, "description": "общая библиотека"})
        client.ids = {"proj": local_only, "shared": cloud_ok}
        yield client


def mcp_call(hub, token, work):
    """`work(client)` with an MCP client of the official SDK, over Streamable HTTP, in the hub's event loop."""

    async def run():
        transport = httpx2.ASGITransport(app=hub.app)
        headers = {"Authorization": f"Bearer {token}"}
        async with (
            httpx2.AsyncClient(transport=transport, base_url=BASE, headers=headers) as http,
            Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client,
        ):
            return await work(client)

    return hub.portal.call(run)


def text(result):
    return "\n".join(block.text for block in result.content)


def test_a_token_is_needed(hub):
    assert hub.post("/mcp", headers={"Authorization": ""}, json={}).status_code == 401
    assert hub.post("/mcp", headers={"Authorization": "Bearer wrong"}, json={}).status_code == 401


def test_a_local_client_reads_the_ecosystem(hub):
    token = hub.app.state.db.call(lambda c: create_client(c, "cline", cloud=False))

    async def work(client):
        tools = await client.list_tools()
        found = {
            "tools": {t.name: t for t in tools.tools},
            "map": text(await client.call_tool("get_ecosystem_map", {})),
            "card": text(await client.call_tool("get_project_card", {"project": "proj"})),
            "symbol": text(await client.call_tool("find_symbol", {"project": "proj", "name": "serve"})),
            "file": text(await client.call_tool("read_file", {"project": "proj", "path": "src/app.py"})),
            "log": text(await client.call_tool("git_log", {"project": "proj", "count": 5})),
            "wrong": await client.call_tool("read_file", {"project": "nope", "path": "x"}),
        }
        return found

    found = mcp_call(hub, token, work)
    assert set(found["tools"]) == TOOLS
    assert all(t.annotations.read_only_hint for t in found["tools"].values())  # D17: all of them only read
    assert "- proj [local-only]" in found["map"] and "- shared: общая библиотека" in found["map"]
    assert '<project name="proj">' in found["card"]
    assert "src/app.py:3-4 function serve: def serve(port):" in found["symbol"]
    assert "[REDACTED]" in found["file"] and SECRET not in found["file"]  # the secret filter, as for models
    assert ": first" in found["log"]
    assert found["wrong"].is_error and "no project 'nope'" in text(found["wrong"])


def test_a_cloud_client_sees_local_only_projects_by_name(hub):
    token = hub.app.state.db.call(lambda c: create_client(c, "claude-code"))  # cloud until marked local (D32)

    async def work(client):
        resources = await client.list_resources()
        return {
            "map": text(await client.call_tool("get_ecosystem_map", {})),
            "read": await client.call_tool("read_file", {"project": "proj", "path": "README.md"}),
            "shared": text(await client.call_tool("file_outline", {"project": "shared", "path": "lib.py"})),
            "resources": [r.uri for r in resources.resources],
            "map_resource": await client.read_resource("hub://map"),
        }

    found = mcp_call(hub, token, work)
    assert "- proj [local-only: you may not see more of it]" in found["map"]
    assert found["read"].is_error and "among those you may read: shared" in text(found["read"])
    assert "class Shared" in found["shared"]
    assert "hub://map" in found["resources"]
    assert "- shared: общая библиотека" in found["map_resource"].contents[0].text


def test_events_and_cards_over_mcp(hub):
    token = hub.app.state.db.call(lambda c: create_client(c, "local-agent", cloud=False))
    pid = hub.ids["proj"]
    payload = {"project_id": pid, "kind": "commit", "branch": "main", "sha": "abc1234", "subject": "fix the port"}
    hub.portal.call(lambda: hub.app.state.bus.publish("git.head", payload, project_id=pid))
    hub.put(f"/v1/projects/{pid}/card", json={"text": "## Назначение\nДемо-проект."})
    hub.post(f"/v1/projects/{pid}/notes", json={"text": "тесты: pytest"})

    async def work(client):
        return {
            "events": text(await client.call_tool("get_events", {"project": "proj"})),
            "card": text(await client.call_tool("get_project_card", {"project": "proj"})),
            "resource": await client.read_resource(f"hub://project/{pid}/card"),
        }

    found = mcp_call(hub, token, work)
    assert "UTC proj: HEAD of main is now abc1234 'fix the port'" in found["events"]
    assert "Демо-проект." in found["card"] and "тесты: pytest" in found["card"]
    assert found["resource"].contents[0].text == "## Назначение\nДемо-проект."
