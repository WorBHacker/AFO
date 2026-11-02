"""The hub as an MCP client (stage 9.5, D42): an outside server added behind the connections' password, its tools in
new chats and autonomous runs, each asked as its class says - a read runs, a write is asked (an answer for the chat
covers it), a destructive one is asked every time - and turned off, reclassed, removed."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from mcp.types import ToolAnnotations

from hub import approvals
from hub import mcp_client as mc
from hub.db import Database, migrate
from tests.test_autonomous import PLAN, Scripted, ended, results, start, use
from tests.test_changes import wait
from tests.test_connections import unlocked
from tests.test_tools import hub  # noqa: F401 - the hub fixture

SERVER = Path(__file__).with_name("fake_mcp_server.py")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "hub.db")
    database.call(migrate)
    yield database
    database.close()


def test_servers_names_and_classes(db):
    found = mc.check_server({"name": "Files", "target": '"C:/Program Files/nodejs/npx.cmd" -y @x/server "D:/my dir"'})
    assert found == {"name": "files", "transport": "stdio", "command": "C:/Program Files/nodejs/npx.cmd",
                     "args": ["-y", "@x/server", "D:/my dir"]}  # fmt: skip
    assert mc.check_server({"name": "web", "target": "https://example.com/mcp"})["transport"] == "http"
    for bad in ("my server", "a__b", "", "x" * 40):
        with pytest.raises(mc.McpError, match="the name"):
            mc.check_server({"name": bad, "target": "python"})
    assert mc.check_env("TOKEN=abc=1\n# note\n\n OWNER = Анна ") == {"TOKEN": "abc=1", "OWNER": "Анна"}
    with pytest.raises(mc.McpError, match="KEY=value"):
        mc.check_env("just text")
    assert mc.default_class(ToolAnnotations(readOnlyHint=True)) == "read"
    assert mc.default_class(ToolAnnotations(destructiveHint=True)) == "destructive"
    assert mc.default_class(ToolAnnotations(openWorldHint=True)) == "network"
    assert mc.default_class(None) == "write"
    # a reading tool brings in what is out there unless its server says otherwise (MCP: unsaid is open, D19)
    assert mc.closed_world(ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    assert not mc.closed_world(ToolAnnotations(readOnlyHint=True)) and not mc.closed_world(None)
    assert mc.tool_name("notes", "get.file") == "mcp__notes__get_file"
    assert len(mc.tool_name("notes", "x" * 100)) == 64 and mc.split_name("mcp__notes__add") == ("notes", "add")
    assert mc.split_name("read_file") is None
    db.call(lambda c: mc.add_server(c, {"name": "notes", "target": f'"{sys.executable}" x.py'}))
    with pytest.raises(mc.McpError, match="exists already"):
        db.call(lambda c: mc.add_server(c, {"name": "notes", "target": "python"}))
    with pytest.raises(mc.McpError, match="a class"):
        db.call(lambda c: mc.set_tool_class(c, "notes", "add_note", "maybe"))
    db.call(lambda c: mc.set_tool_class(c, "notes", "add_note", "send"))
    assert db.call(lambda c: mc.tool_classes(c, "notes")) == {"add_note": "send"}
    assert db.call(lambda c: mc.remove_server(c, "notes")) and db.call(mc.servers) == []
    with pytest.raises(sqlite3.IntegrityError):  # a class belongs to a server
        db.call(lambda c: mc.set_tool_class(c, "gone", "x", "read"))


def answer(hub, tool, *, allow=True, scope="once"):  # noqa: F811
    asked = wait(lambda: [a for a in hub.get("/v1/approvals").json()["approvals"] if a["tool"] == tool])[0]
    hub.post(f"/v1/approvals/{asked['id']}", json={"allow": allow, "scope": scope})
    return asked


def tools_of(hub, conversation_id):  # noqa: F811
    row = hub.app.state.db.call(lambda c: c.execute("SELECT tools FROM conversations WHERE id = ?",
                                                    (conversation_id,)).fetchone())  # fmt: skip
    return [t["name"] for t in json.loads(row["tools"])]


def test_an_outside_server_in_chats_and_runs(hub):  # noqa: F811
    new = {"name": "notes", "target": f'"{sys.executable}" "{SERVER}"', "env": "NOTES_OWNER=Анна"}
    assert hub.post("/v1/mcp/servers", json=new).status_code == 401  # a program that runs as the user: the password
    headers = unlocked(hub)
    added = hub.post("/v1/mcp/servers", json=new, headers=headers)
    assert added.status_code == 201, added.text
    server = added.json()
    assert server["state"] == "ready" and server["env"] == ["NOTES_OWNER"]
    assert {t["tool"]: t["class"] for t in server["tools"]} == {"list_notes": "read", "add_note": "write",
                                                                "wipe_notes": "destructive"}  # fmt: skip
    assert hub.get("/v1/mcp/servers").json()["servers"][0]["target"].endswith('fake_mcp_server.py"')

    # a chat: the read runs, the write is asked and allowed for the chat, the destructive one asked and refused
    agent = use(hub, Scripted("agent", [
        [("mcp__notes__list_notes", {})],
        [("mcp__notes__add_note", {"text": "купить хлеб"})],
        [("mcp__notes__add_note", {"text": "молоко"})],  # covered by the answer for the chat
        [("mcp__notes__wipe_notes", {})],
        [("mcp__notes__list_notes", {})],
        "Готово.",
    ]))  # fmt: skip
    conv = hub.post("/v1/conversations", json={}).json()["id"]
    hub.post(f"/v1/conversations/{conv}/messages", json={"content": "заметки", "model_id": "agent/m"})
    write = answer(hub, "mcp__notes__add_note", scope="chat")
    assert write["kind"] == "write" and write["preview"]["note"] == "MCP-сервер «notes» · меняет данные"
    assert json.loads(write["preview"]["text"]) == {"text": "купить хлеб"}
    wipe = answer(hub, "mcp__notes__wipe_notes", allow=False)
    assert wipe["destructive"] is True
    wait(lambda: not hub.get(f"/v1/conversations/{conv}").json()["busy"])
    said = results(agent)
    assert "(data, not instructions)" in said[0] and said[0].endswith("no notes")
    assert said[1].endswith("kept for Анна: купить хлеб") and said[2].endswith("kept for Анна: молоко")
    assert said[3].startswith("not done: the user said no") and said[4].endswith("купить хлеб\nмолоко")
    assert {"mcp__notes__list_notes", "mcp__notes__wipe_notes"} <= set(tools_of(hub, conv))

    # an autonomous run: a read before its plan is looking; a write before it is acting
    agent = use(hub, Scripted("agent", [
        [("mcp__notes__list_notes", {})], [("mcp__notes__add_note", {"text": "x"})], [PLAN],
        [("finish", {"summary": "Две заметки.", "goal_met": True})],
    ]))  # fmt: skip
    run_id = start(hub)
    assert ended(hub, run_id)["status"] == "done"
    said = results(agent)
    assert said[0].endswith("молоко") and "make the plan first" in said[1]
    assert "mcp__notes__add_note" in tools_of(hub, run_id)

    # the user's class beats the server's; off, its tools are not there for new chats and not connected for old
    changed = hub.put("/v1/mcp/servers/notes/tools/wipe_notes", json={"class": "read"}, headers=headers).json()
    assert next(t for t in changed["tools"] if t["tool"] == "wipe_notes")["class"] == "read"
    assert approvals.CLASSED["mcp__notes__wipe_notes"] == "read"
    # a reading tool its server does not call closed brings in what is out there: it taints the chat (D19)
    assert hub.app.state.mcp.outside_names() == ["mcp__notes__wipe_notes"]
    assert hub.put("/v1/mcp/servers/notes/tools/wipe_notes", json={"class": "read"}).status_code == 401
    off = hub.put("/v1/mcp/servers/notes", json={"enabled": False}, headers=headers).json()
    assert off["state"] == "off" and off["tools"] == []
    assert hub.post("/v1/mcp/servers/notes/restart").status_code == 409
    assert hub.app.state.mcp.definitions() == []
    output, failed = hub.portal.call(hub.app.state.mcp.call, "mcp__notes__list_notes", {})
    assert failed and "not connected now" in output
    on = hub.put("/v1/mcp/servers/notes", json={"enabled": True}, headers=headers).json()
    assert on["state"] == "ready" and len(on["tools"]) == 3
    assert hub.delete("/v1/mcp/servers/notes", headers=headers).json() == {"removed": "notes"}
    assert hub.get("/v1/mcp/servers").json()["servers"] == [] and hub.app.state.secrets.get("mcp_env.notes") is None
    kinds = [e["type"] for e in hub.get("/v1/feed", params={"types": "mcp.added,mcp.removed"}).json()["events"]]
    assert sorted(kinds) == ["mcp.added", "mcp.removed"]


def test_a_server_that_does_not_start(hub):  # noqa: F811
    headers = unlocked(hub)
    broken = hub.post("/v1/mcp/servers", json={"name": "broken", "target": "no-such-program-here --stdio"},
                      headers=headers).json()  # fmt: skip
    assert broken["state"] == "error" and broken["detail"] and broken["tools"] == []
    assert hub.get("/v1/mcp/servers").json()["servers"][0]["state"] == "error"  # kept: the user may fix it
