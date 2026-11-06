"""The symbol index (stage 3, D26): definitions by tree-sitter, kept up to date, the tools; frozen tool lists."""

import asyncio
import dataclasses
import json
import os
import shutil
import sys

import pytest
from starlette.testclient import TestClient

from common.paths import data_paths
from hub import store, symbols
from hub.chat import CHAT_TOOLS
from hub.db import Database, available_migrations, migrate
from hub.gateway.base import Model, StreamEvent
from hub.projects import Project
from hub.server import create_app
from hub.settings import HubSettings
from hub.symbols import SymbolIndex, key
from hub.tools import FIRST_TOOLS, TOOLS, ToolContext, run_tool

migrate_module = sys.modules["hub.db.migrate"]

SECRET = "sk-ant-api03-" + "q" * 40

SAMPLES = {
    "python": (
        b"LIMIT = 5\nclass Hub:\n    def start(self):\n        def inner():\n            pass\n\n"
        b"    @property\n    def name(self):\n        return 1\n\ndef main():\n    pass\n",
        [("constant", "", "LIMIT", 1), ("class", "", "Hub", 2), ("method", "Hub", "start", 3),
         ("function", "Hub.start", "inner", 4), ("method", "Hub", "name", 8), ("function", "", "main", 11)],
    ),
    "typescript": (
        b"export interface Shape {\n  area(): number\n}\nexport class Circle implements Shape {\n"
        b"  area(): number { return 1 }\n}\nexport function make(): Circle { return new Circle() }\n"
        b"const helper = (x: number) => x\nnamespace Geo { export function scale() {} }\n",
        [("interface", "", "Shape", 1), ("method", "Shape", "area", 2), ("class", "", "Circle", 4),
         ("method", "Circle", "area", 5), ("function", "", "make", 7), ("function", "", "helper", 8),
         ("module", "", "Geo", 9), ("method", "Geo", "scale", 9)],
    ),
    "go": (
        b"package main\n\ntype Server struct{}\n\nfunc (s *Server) Start() error { return nil }\n\nfunc main() {}\n",
        [("type", "", "Server", 3), ("method", "", "Start", 5), ("function", "", "main", 7)],
    ),
    "rust": (
        b"struct Hub;\nimpl Hub {\n    fn start(&self) {}\n}\ntrait Run { fn run(&self); }\nfn main() {}\n",
        [("class", "", "Hub", 1), ("impl", "", "Hub", 2), ("method", "Hub", "start", 3), ("interface", "", "Run", 5),
         ("function", "", "main", 6)],
    ),
    "csharp": (
        b"namespace Demo {\n  public class Svc {\n    public void Run() {}\n  }\n}\n",
        [("module", "", "Demo", 1), ("class", "Demo", "Svc", 2), ("method", "Demo.Svc", "Run", 3)],
    ),
    "java": (
        b"public class App {\n  public void run() {}\n}\n",
        [("class", "", "App", 1), ("method", "App", "run", 2)],
    ),
    "cpp": (
        b"namespace net {\nclass Conn {\n public:\n  void open();\n};\n}\nint main() { return 0; }\n",
        [("class", "", "Conn", 2), ("method", "Conn", "open", 4), ("function", "", "main", 7)],
    ),
}  # fmt: skip


@pytest.mark.parametrize("language", SAMPLES)
def test_definitions_in_each_language(language):
    source, expected = SAMPLES[language]
    found = symbols.parse(source, language)
    assert [(s.kind, s.container, s.name, s.line) for s in found] == expected
    assert all(s.end_line >= s.line for s in found)


def test_signature_and_lines():
    source = "class Hub:\n    def start(self, port: int = 8765) -> None:\n        pass\n        return\n"
    hub, start = symbols.parse(source.encode(), "python")
    assert (start.line, start.end_line) == (2, 4) and (hub.line, hub.end_line) == (1, 4)
    assert start.signature == "def start(self, port: int = 8765) -> None:"
    cyrillic = "# Привет\nОКНО = 1\ndef открыть():\n    pass\n"  # lines count right after multi-byte text
    assert [(s.name, s.line) for s in symbols.parse(cyrillic.encode(), "python")] == [("ОКНО", 2), ("открыть", 3)]


def project(root, pid="p1", name="proj"):
    return Project(id=pid, name=name, path=str(root), description="", cloud_ok=False, created_at="", updated_at="")


@pytest.fixture
def code(repo):
    (repo / "src" / "app.py").write_text(
        "class ChatService:\n    def send(self):\n        pass\n\n    def _steps(self):\n        pass\n\n"
        "def send_all():\n    pass\n",
        encoding="utf-8",
    )
    (repo / "src" / "client.ts").write_text("export class Client {\n  send() {}\n}\n", encoding="utf-8")
    (repo / ".env").write_text(f"KEY={SECRET}\n", encoding="utf-8")  # a secret file
    (repo / "config.py").write_text(f"API_KEY = '{SECRET}'\nTIMEOUT = 5\n", encoding="utf-8")
    (repo / "build" / "gen.py").write_text("def generated():\n    pass\n", encoding="utf-8")  # ignored by git
    return project(repo.resolve())


def test_index_finds_and_follows_changes(code):
    index = SymbolIndex(fresh_s=0)
    state = index.refresh(code)
    assert set(state.files) == {"src/app.py", "src/client.ts", "config.py"} and state.complete
    found = index.find(code, "send")
    assert [(s["path"], s["container"], s["name"]) for s in found["symbols"]] == [
        ("src/app.py", "ChatService", "send"),  # exact names first, then shorter ones, then by place
        ("src/client.ts", "Client", "send"),
        ("src/app.py", "", "send_all"),
    ]
    assert found["total"] == 3 and found["complete"]
    assert [s["name"] for s in index.find(code, "SEND")["symbols"]][:2] == ["send", "send"]  # case is ignored
    assert [s["name"] for s in index.find(code, "steps")["symbols"]] == ["_steps"]  # a part of the name
    assert [s["path"] for s in index.find(code, "Client.send")["symbols"]] == ["src/client.ts"]
    assert [s["name"] for s in index.find(code, "send", kind="function")["symbols"]] == ["send_all"]
    assert index.find(code, "send", limit=1)["total"] == 3
    assert [s["path"] for s in index.find(code, "KEY")["symbols"]] == ["config.py"]
    assert [s["name"] for s in index.outline(code, "src/app.py")] == ["ChatService", "send", "_steps", "send_all"]
    assert index.outline(code, "build/gen.py")[0]["name"] == "generated"  # outside the index: read directly
    assert index.outline(code, ".env") is None and index.outline(code, "README.md") is None

    app = code.root / "src" / "app.py"
    app.write_text("def renamed():\n    pass\n", encoding="utf-8")
    os.utime(app, ns=(app.stat().st_atime_ns, app.stat().st_mtime_ns + 10_000_000))
    assert [s["name"] for s in index.find(code, "send")["symbols"]] == ["send"]  # only the TypeScript one now
    assert index.find(code, "renamed")["symbols"][0]["line"] == 1
    (code.root / "src" / "client.ts").unlink()
    assert index.find(code, "send")["total"] == 0
    index.forget(code.id)
    assert index.refresh(code).files.keys() == {"src/app.py", "config.py"}


def test_a_copy_of_a_project_has_an_index_of_its_own(code, tmp_path):
    """19.4: a sandbox's copy has the project's id and a root of its own: what only the copy defines stays out of the
    project's index - by name and by meaning -, and forgetting the copy leaves the project's."""
    copy = dataclasses.replace(code, path=str(tmp_path / "copy"))
    shutil.copytree(code.root, copy.root)
    (copy.root / "src" / "only.py").write_text('def copy_only():\n    """Lives in the copy alone."""\n',
                                               encoding="utf-8")  # fmt: skip
    index = SymbolIndex(fresh_s=0)
    assert index.find(copy, "copy_only")["total"] == 1 and index.find(copy, "copy alone")["symbols"]
    assert index.find(code, "copy_only")["total"] == 0 and index.find(code, "copy alone")["symbols"] == []
    assert index.find(code, "send")["total"] == index.find(copy, "send")["total"] == 3
    index.forget(code.id, copy.root)
    assert index.meaning.count(key(copy)) == 0 and index.meaning.count(key(code)) > 0
    index.forget(code.id)
    assert index.meaning.count(key(code)) == 0


def test_a_big_project_is_indexed_in_parts(code):
    index = SymbolIndex(budget_s=0, fresh_s=60)
    first = index.find(code, "send")
    assert not first["complete"]  # the time budget ran out before the files were read
    index._budget_s = 10
    assert index.find(code, "send")["complete"]  # the rest on the next query, even within fresh_s
    assert index.find(code, "send")["total"] == 3


def test_a_project_bigger_than_the_index_says_so(code, monkeypatch):
    """Files past MAX_FILES are never read: the index says it is not complete, and how much it left out (D46) - but
    it does not read the project again on every query, as an unfinished one would."""
    from app.ui import project_rows
    from hub import search

    files = search.project_files(code.root)
    monkeypatch.setattr(symbols, "MAX_FILES", 2)
    index = SymbolIndex(fresh_s=60)
    found = index.find(code, "send")
    assert not found["complete"] and found["left_out"] == len(files) - 2
    looked = []
    monkeypatch.setattr(search, "project_files", lambda root: looked.append(root) or files)
    index.find(code, "send")
    assert looked == []  # fresh: not read again
    ctx = ToolContext([code], symbols=index)
    text, error = tool("find_symbol", {"project": "proj", "name": "send"}, ctx)
    assert not error and f"{len(files) - 2} are left out" in text
    assert "не прочитаны" in project_rows.search_summary(found)


# --- the tools ---


def tool(name, args, ctx):
    return asyncio.run(run_tool(name, args, ctx))


def test_find_symbol_and_file_outline_tools(code):
    ctx = ToolContext([code])
    text, error = tool("find_symbol", {"project": "proj", "name": "send"}, ctx)
    assert not error and text.splitlines() == [
        "3 definitions match 'send' in proj:",
        "src/app.py:2-3 method ChatService.send: def send(self):",
        "src/client.ts:2 method Client.send: send() {}",
        "src/app.py:8-9 function send_all: def send_all():",
    ]
    masked, _ = tool("find_symbol", {"project": "proj", "name": "API_KEY"}, ctx)
    assert "config.py:1 constant API_KEY" in masked and SECRET not in masked and "[REDACTED]" in masked
    none, error = tool("find_symbol", {"project": "proj", "name": "nothing_like_it"}, ctx)
    assert not error and none == "no definitions named like 'nothing_like_it' in proj"
    _, error = tool("find_symbol", {"project": "proj", "name": "send", "kind": "lambda"}, ctx)
    assert error

    outline, error = tool("file_outline", {"project": "proj", "path": "src\\app.py"}, ctx)
    assert not error and outline.splitlines() == [
        "src/app.py - 4 definitions (line or lines, kind, name, first line):",
        "1-6 class ChatService: class ChatService:",
        "  2-3 method send: def send(self):",
        "  5-6 method _steps: def _steps(self):",
        "8-9 function send_all: def send_all():",
    ]
    for path, words in [(".env", "holds secrets"), ("README.md", "read it with read_file"),
                        ("src/none.py", "no file"), ("../outside.py", "leaves the project")]:  # fmt: skip
        text, error = tool("file_outline", {"project": "proj", "path": path}, ctx)
        assert error and words in text, (path, text)


# --- a conversation keeps its tool list (D14, migration 010) ---


def test_a_conversation_keeps_the_tools_it_was_first_sent(monkeypatch):
    db = Database(data_paths().ensure().db)
    real = available_migrations()
    monkeypatch.setattr(migrate_module, "available_migrations", lambda: [m for m in real if m[0] < 10])
    db.call(migrate)

    def conversation(cid, *messages):
        def write(conn):
            conn.execute("INSERT INTO conversations (id, kind) VALUES (?, 'chat')", (cid,))
            for seq, (role, model) in enumerate(messages):
                conn.execute("INSERT INTO messages (conversation_id, seq, role, model_id, content) VALUES "
                             "(?, ?, ?, ?, '[]')", (cid, seq, role, model))  # fmt: skip

        db.call(write)

    conversation("claude", ("user", None), ("assistant", "anthropic/claude-opus-5-5"))
    conversation("context", ("user", None), ("system", None), ("assistant", "llama-swap/coder"))
    conversation("local", ("user", None), ("assistant", "llama-swap/coder"))  # never sent tools
    monkeypatch.setattr(migrate_module, "available_migrations", lambda: real)
    db.call(migrate)
    marks = dict(db.call(lambda c: c.execute("SELECT id, tools FROM conversations").fetchall()))
    assert marks == {"claude": "v1", "context": "v1", "local": None}

    first = [t["name"] for t in db.call(lambda c: store.conversation_tools(c, "claude", TOOLS, FIRST_TOOLS))]
    assert first == list(FIRST_TOOLS) and "find_symbol" not in first
    assert [t["name"] for t in db.call(lambda c: store.conversation_tools(c, "local", TOOLS, FIRST_TOOLS))] == [
        t["name"] for t in TOOLS
    ]
    # later the hub's tools change - a description, a new tool: what a conversation was sent stays the same
    changed = [{**t, "description": "changed"} for t in TOOLS] + [
        {"name": "new_tool", "description": "", "parameters": {}}
    ]
    assert db.call(lambda c: store.conversation_tools(c, "claude", changed, FIRST_TOOLS)) == [
        t for t in TOOLS if t["name"] in FIRST_TOOLS
    ]
    assert db.call(lambda c: store.conversation_tools(c, "local", changed, FIRST_TOOLS)) == json.loads(
        json.dumps(TOOLS)
    )
    db.close()


# --- through the hub ---


class ToolProvider:
    name = "fake"

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.models = [Model("fake/echo", "fake", "echo", "Echo", "local", {}, {})]

    async def list_models(self):
        return self.models

    async def stream_chat(self, model_ref, messages, effort, cancel, tools=None):
        self.calls.append((messages, tools))
        if tools and self.steps:
            yield StreamEvent("end", usage={"input_tokens": 1, "output_tokens": 1}, stop_reason="tool_use",
                              tool_calls=self.steps.pop(0))  # fmt: skip
            return
        yield StreamEvent("content", "ок")
        yield StreamEvent("end", usage={"input_tokens": 1, "output_tokens": 1}, stop_reason="end_turn")


def test_symbols_through_the_hub(code):
    app = create_app(
        HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0, process_interval_s=0)
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as hub:
        hub.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        pid = hub.post("/v1/projects", json={"path": str(code.root)}).json()["id"]
        found = hub.get(f"/v1/projects/{pid}/symbols", params={"q": "send"}).json()
        assert [s["name"] for s in found["symbols"]] == ["send", "send", "send_all"] and found["complete"]
        outline = hub.get(f"/v1/projects/{pid}/symbols", params={"path": "src/app.py"}).json()
        assert outline["symbols"][0] == {"name": "ChatService", "kind": "class", "line": 1, "end_line": 6,
                                         "container": "", "signature": "class ChatService:"}  # fmt: skip
        assert hub.get(f"/v1/projects/{pid}/symbols").status_code == 422
        assert hub.get(f"/v1/projects/{pid}/symbols", params={"path": "README.md"}).status_code == 400

        # a new chat gets the symbol tools, and the model finds a definition with them
        provider = ToolProvider([[{"id": "c1", "name": "find_symbol", "input": {"project": "proj", "name": "_steps"}}]])
        app.state.gateway.register(provider)
        hub.portal.call(app.state.gateway.sync_catalog)
        conv = hub.post("/v1/conversations", json={}).json()["id"]
        hub.put(f"/v1/conversations/{conv}/projects", json={"project_ids": [pid]})
        hub.post(f"/v1/conversations/{conv}/messages", json={"content": "где _steps?", "model_id": "fake/echo"})
        for _ in range(300):
            messages = hub.get(f"/v1/conversations/{conv}").json()["messages"]
            if len(messages) == 5:
                break
            hub.portal.call(asyncio.sleep, 0.02)
        assert [t["name"] for t in provider.calls[0][1]] == [t["name"] for t in CHAT_TOOLS]
        assert provider.calls[1][1] == provider.calls[0][1]  # the same list in every request
        assert messages[3]["content"][0]["output"].endswith(
            "src/app.py:5-6 method ChatService._steps: def _steps(self):"
        )
        hub.delete(f"/v1/projects/{pid}")
        assert pid not in app.state.symbols._projects  # forgotten with the project
