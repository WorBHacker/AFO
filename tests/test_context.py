"""Context layers (stage 3, D22): the map, a project, the live state, what goes into a stored context message, and
how each provider gets a system message inside the conversation."""

from datetime import UTC, datetime

from hub import context
from hub.gateway.anthropic_provider import to_wire
from hub.gateway.openai_compat import wire_messages
from hub.projects import Project

NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)


def project(root, name="proj", pid="p1", cloud_ok=False, description=""):
    return Project(id=pid, name=name, path=str(root), description=description, cloud_ok=cloud_ok, created_at="",
                   updated_at="")  # fmt: skip


def test_the_map_and_a_project(repo, tmp_path):
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("Run tests with pytest.\nAPI_KEY=sk-ant-api03-" + "z" * 40 + "\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    other = tmp_path / "web"
    other.mkdir()
    (other / "package.json").write_text("{}", encoding="utf-8")
    mine = project(repo.resolve(), description="бэкенд")
    web = project(other, name="web", pid="p2", cloud_ok=True, description="фронтенд")

    the_map = context.map_layer([web, mine])
    assert the_map.splitlines()[1:] == ["- proj [local-only]: бэкенд (Python)", "- web: фронтенд (Node.js)"]
    assert "[local-only: you may not see more of it]" in context.map_layer_for_cloud([web, mine])

    layer = context.project_layer(mine)
    assert layer.startswith('<project name="proj">') and "Stack (from its files): Python" in layer
    assert "README.md (start):\n# Проект" in layer and "CLAUDE.md (the project's own notes" in layer
    assert "Run tests with pytest." in layer and "[REDACTED]" in layer and "sk-ant" not in layer
    assert ".env" not in layer  # a secret file is not even listed
    assert "Git: a repository, branch main" in layer


def test_the_live_state(repo):
    mine = project(repo.resolve())
    events = [
        {"ts": "2026-10-27T02:50:00Z", "type": "port.opened", "payload": {"port": 8000, "name": "python.exe"}},
        {"ts": "2026-10-27T02:55:00Z", "type": "terminal.exited",
         "payload": {"title": "pytest", "exit_code": 1, "stopped": False}},
        {"ts": "2026-10-27T02:56:00Z", "type": "chat.message", "payload": {}},  # not news
    ]  # fmt: skip
    processes = [{"name": "python.exe", "pid": 5, "ports": [8000]}, {"name": "hub", "ports": [8765], "hub": True}]
    terminals = [{"id": "t1", "title": "pytest", "status": "exited", "exit_code": 1}]
    live = context.live_layer(mine, processes=processes, terminals=terminals, events=events, now=NOW)
    assert "git: branch main, 2 uncommitted change(s)" in live and " M src/app.py" in live
    assert "listening: python.exe on :8000" in live and "8765" not in live
    assert "terminal 'pytest' (id t1) ended with code 1" in live
    assert "10 min ago: port 8000 opened by python.exe" in live and "5 min ago: terminal 'pytest' exited" in live
    assert "chat.message" not in live


def test_a_context_message_holds_what_changed(repo):
    mine = project(repo.resolve())
    live = {"p1": '<live_state project="proj">\nnothing\n</live_state>'}
    first = context.context_blocks(all_projects=[mine], attached=[mine], previous={}, live=live)
    assert [b["key"] for b in first] == ["header", "map", "project:p1", "live:p1", "tools"]

    class Row:
        role = "system"
        content = first

    again = context.context_blocks(all_projects=[mine], attached=[mine], previous=context.previous_digests([Row]),
                                   live=live)  # fmt: skip
    assert [b["key"] for b in again] == ["header", "live:p1", "tools"]  # nothing else changed

    local = context.render(first, cloud=False)
    cloud = context.render(first, cloud=True)
    assert '<project name="proj">' in local and '<project name="proj">' not in cloud
    assert "[proj: local-only, details hidden from cloud models]" in cloud
    assert context.tokens("x" * 400) == 100


def test_a_system_message_inside_the_conversation_on_the_wire():
    messages = [
        {"role": "user", "content": "вопрос"},
        {"role": "system", "content": "контекст"},
        {"role": "assistant", "content": "ответ"},
    ]
    _system, turns = to_wire(messages, "anthropic")
    assert [t["role"] for t in turns] == ["user", "system", "assistant"]  # Opus, Sonnet, Fable
    assert turns[1] == {"role": "system", "content": "контекст"}
    _system, turns = to_wire(messages, "anthropic", mid_system=False)  # Haiku
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"][-1] == {"type": "text", "text": "<system-reminder>\nконтекст\n</system-reminder>"}
    system, turns = to_wire([{"role": "system", "content": "в начале"}, *messages[:1]], "anthropic")
    assert system == "в начале" and turns[0]["role"] == "user"

    local = wire_messages(messages)
    assert [m["role"] for m in local] == ["user", "assistant"]
    assert local[0]["content"] == "вопрос\n\n<context>\nконтекст\n</context>"
    assert wire_messages(messages) == local  # the same history, the same bytes
