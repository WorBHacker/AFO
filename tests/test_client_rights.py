"""What a client other than the window may do (D31, D32; the audit, D46): read the projects as MCP lets it, begin and
continue conversations of its own, follow the hub's state - and nothing of the window's."""

from hub.auth import create_client
from tests.test_autonomous import Scripted, use
from tests.test_changes import wait
from tests.test_tools import SECRET, WS_HOST, hub  # noqa: F401 - the hub fixture


def test_another_client_reaches_only_what_it_may(hub, repo):  # noqa: F811
    token = hub.app.state.db.call(lambda c: create_client(c, "cline"))  # cloud, as every client until marked local
    other = {"Authorization": f"Bearer {token}"}

    # the window's ground: an agent's schedule, approvals, terminals, quiet hours, the models
    for method, path in (("get", "/v1/approvals"), ("post", "/v1/schedules"), ("get", "/v1/terminals"),
                         ("put", "/v1/agent/quiet"), ("post", "/v1/models/unload"), ("get", "/v1/runs")):  # fmt: skip
        sent = {"json": {}} if method in ("post", "put") else {}
        assert getattr(hub, method)(path, headers=other, **sent).status_code == 403, path
    assert hub.get("/v1/info", headers=other).json()["client"]["name"] == "cline"

    # the projects as MCP has them: a local-only one is not there for a cloud client; no secret file, no secret
    (repo / "config.py").write_text(f"KEY = '{SECRET}'\n", encoding="utf-8")
    (repo / ".env").write_text(f"ANTHROPIC_API_KEY={SECRET}\n", encoding="utf-8")
    pid = hub.post("/v1/projects", json={"path": str(repo)}).json()["id"]
    assert hub.get("/v1/projects", headers=other).json()["projects"] == []
    read = f"/v1/projects/{pid}/file"
    assert hub.get(read, params={"path": "README.md"}, headers=other).status_code == 404
    hub.patch(f"/v1/projects/{pid}", json={"cloud_ok": True})
    assert [p["id"] for p in hub.get("/v1/projects", headers=other).json()["projects"]] == [pid]
    text = hub.get(read, params={"path": "config.py"}, headers=other).json()["text"]
    assert SECRET not in text and "[REDACTED]" in text
    assert SECRET in hub.get(read, params={"path": "config.py"}).json()["text"]  # the window shows the file as it is
    assert hub.get(read, params={"path": ".env"}, headers=other).status_code == 403
    found = hub.get(f"/v1/projects/{pid}/search", params={"q": "sk-ant"}, headers=other).json()["files"]
    assert all(f["path"] != ".env" and SECRET not in m["text"] for f in found for m in f["matches"])

    # conversations: its own only - the window's is not there for it
    use(hub, Scripted("agent", ["Ответ окну."]))
    mine = hub.post("/v1/conversations", json={}, headers=other).json()["id"]
    windows = hub.post("/v1/conversations", json={}).json()["id"]
    assert [c["id"] for c in hub.get("/v1/conversations", headers=other).json()["conversations"]] == [mine]
    assert {mine, windows} <= {c["id"] for c in hub.get("/v1/conversations").json()["conversations"]}
    say = {"content": "привет", "model_id": "agent/m"}
    assert hub.get(f"/v1/conversations/{windows}", headers=other).status_code == 404
    assert hub.post(f"/v1/conversations/{windows}/messages", json=say, headers=other).status_code == 404
    assert hub.delete(f"/v1/conversations/{windows}", headers=other).status_code == 404

    # the events: its own conversation's, never the window's
    with hub.websocket_connect("/v1/events", headers={**other, **WS_HOST}) as events:
        assert events.receive_json()["type"] == "hub.hello"
        hub.post(f"/v1/conversations/{windows}/messages", json={**say, "content": "тайна окна"})
        wait(lambda: not hub.get(f"/v1/conversations/{windows}").json()["busy"])
        assert hub.post(f"/v1/conversations/{mine}/messages", json=say, headers=other).status_code == 202
        seen = []
        while not (seen and seen[-1]["type"] == "chat.done"):
            seen.append(events.receive_json())
    assert {e["payload"].get("conversation_id") for e in seen if "conversation_id" in e["payload"]} == {mine}
    assert "тайна окна" not in str(seen)
    assert hub.get(f"/v1/conversations/{mine}", headers=other).json()["messages"][-1]["role"] == "assistant"
