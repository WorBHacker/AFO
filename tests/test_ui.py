"""The Qt Quick window: its controller against a fake hub client, its row builders, and the real QML loaded with
history and a streamed answer (no QML warnings allowed). Offscreen, software scene graph."""

import copy
import os
import time
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication as QApplication  # a QML window needs no widgets
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from app import palette
from app.hub_client import HubError
from app.ui import commands, project_rows, rows
from app.ui.controller import Controller
from app.ui.models import TREE_ROLES, RowModel
from app.ui.theme import Theme, qml_colour
from common.paths import data_paths

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)

NOW = datetime.now(UTC)
MODELS = [
    {
        "id": "anthropic/claude-opus-5-5",
        "model_ref": "claude-opus-5-5",
        "name": "Claude Opus 5.5",
        "locality": "cloud",
        "prices": {"input": 4.0, "output": 20.0},
        "capabilities": {"context": 1_000_000},
    },
    {"id": "llama-swap/coder", "model_ref": "coder", "name": "Coder", "locality": "local", "prices": {}},
]
STATUS = {
    "gpu": {"name": "RTX", "vram_used_mib": 8192, "vram_total_mib": 16384, "util_pct": 5, "temp_c": 40},
    "memory": {"ram_used_gib": 30.0, "ram_total_gib": 64.0, "commit_used_gib": 40.0, "commit_limit_gib": 79.0},
    "local": {"reachable": True, "running": [{"model": "coder", "state": "ready"}]},
    "busy": [],
}
HISTORY = {
    "id": "c1",
    "title": "Первый",
    "busy": False,
    "messages": [
        {"id": 1, "role": "user", "model_id": None, "content": [{"type": "text", "text": "привет"}], "usage": None},
        {
            "id": 2,
            "role": "assistant",
            "model_id": "anthropic/claude-opus-5-5",
            "content": [
                {"type": "reasoning", "text": "подумать"},
                {"type": "text", "text": "**Привет!**\n\n```python\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"},
            ],
            "usage": {"output_tokens": 12, "cost_usd": 0.0003, "stats": {"tok_s": 30.0, "think_s": 2.5}},
        },
    ],
}
BUDGET = {
    "id": 1,
    "scope": "total",
    "scope_ref": "",
    "period": "month",
    "soft_usd": 5.0,
    "hard_usd": 10.0,
    "hard_action": "ask",
    "spent_usd": 1.25,
    "state": "ok",
}
REACHED = {
    "id": 1,
    "scope": "total",
    "scope_ref": "",
    "period": "month",
    "soft_usd": 5.0,
    "hard_usd": 10.0,
    "hard_action": "ask",
    "spent_usd": 10.4,
    "state": "hard",
}
PROJECT = {"id": "p1", "name": "LocalChat", "path": "D:/Code/LocalChat", "exists": True}
TREE = {
    "": [
        {"name": "src", "path": "src", "dir": True, "size": 0},
        {"name": "logo.png", "path": "logo.png", "dir": False, "size": 2048},
        {"name": "README.md", "path": "README.md", "dir": False, "size": 12},
    ],
    "src": [
        {"name": "deep", "path": "src/deep", "dir": True, "size": 0},
        {"name": "app.py", "path": "src/app.py", "dir": False, "size": 26},
    ],
    "src/deep": [{"name": "x.txt", "path": "src/deep/x.txt", "dir": False, "size": 1}],
}
FILES = {"src/app.py": "print('hi')\nprint('new')\n", "README.md": "# Проект\n", "src/deep/x.txt": "x"}
FEED = [
    {"id": 9, "ts": "2026-10-27T01:05:00.000Z", "type": "port.opened", "project_id": "p1",
     "payload": {"project_id": "p1", "port": 8000, "pid": 10, "name": "python.exe", "args": "-m http.server 8000"}},
    {"id": 8, "ts": "2026-10-27T01:04:00.000Z", "type": "chat.message", "project_id": None, "payload": {}},
    {"id": 7, "ts": "2026-10-27T01:03:00.000Z", "type": "git.head", "project_id": "p1",
     "payload": {"project_id": "p1", "kind": "commit", "branch": "main", "sha": "abc1234", "subject": "fix",
                 "author": "Test"}},
    {"id": 6, "ts": "2026-10-27T01:02:00.000Z", "type": "chat.done", "project_id": None,
     "payload": {"conversation_id": "c1", "usage": {"output_tokens": 1200}, "cost_usd": 0.0123}},
    {"id": 5, "ts": "2026-10-20T10:00:00.000Z", "type": "terminal.exited", "project_id": "p1",
     "payload": {"terminal_id": "t1", "exit_code": 2, "title": "npm test", "stopped": False}},
]  # fmt: skip
TOOL_HISTORY = {
    "id": "ct",
    "title": "Про проект",
    "busy": False,
    "projects": ["p1"],
    "messages": [
        {"id": 11, "role": "user", "model_id": None, "content": [{"type": "text", "text": "что в README?"}],
         "usage": None},
        {"id": 111, "role": "system", "model_id": None, "usage": None, "content": [
            {"type": "context", "key": "header", "text": "[LocalChat context]"},
            {"type": "context", "key": "map", "text": "Projects on this computer:\n- LocalChat", "digest": "a"},
            {"type": "context", "key": "project:p1", "project": "LocalChat", "cloud_ok": False, "digest": "b",
             "text": '<project name="LocalChat">\nStack: Python\n</project>'},
            {"type": "context", "key": "live:p1", "project": "LocalChat", "cloud_ok": False,
             "text": "<live_state>git: branch main</live_state>"},
            {"type": "context", "key": "tools", "text": "Attached projects: LocalChat."},
        ]},
        {"id": 12, "role": "assistant", "model_id": "llama-swap/coder", "usage": None, "content": [
            {"type": "text", "text": "Смотрю."},
            {"type": "tool_call", "id": "c1", "name": "read_file",
             "input": {"project": "LocalChat", "path": "README.md"}},
            {"type": "tool_call", "id": "c2", "name": "search_code", "input": {"project": "LocalChat", "query": "x"}},
        ]},
        {"id": 13, "role": "tool", "model_id": None, "usage": None, "content": [
            {"type": "tool_result", "id": "c1", "name": "read_file", "output": "README.md (lines 1-1 of 1)\n1| # Hi"},
            {"type": "tool_result", "id": "c2", "name": "search_code", "output": "no project 'x'", "error": True},
        ]},
        {"id": 14, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
         "content": [{"type": "text", "text": "В README заголовок Hi."}]},
    ],
}  # fmt: skip
TERMINAL = {
    "id": "t1", "project_id": "p1", "title": "powershell", "command": "powershell.exe -NoLogo",
    "cwd": "D:\\Code\\LocalChat", "cols": 120, "rows": 30, "pid": 500, "status": "running", "exit_code": None,
    "created_by": "app", "created_at": "2026-10-27T00:00:00.000Z", "ended_at": None, "log_bytes": 10,
    "log_deleted": False,
}  # fmt: skip
TAIL = "PS D:\\Code> "
PROCESSES = {
    "p1": [
        {"pid": 10, "ppid": 1, "name": "python.exe", "exe": "C:\\py\\python.exe", "cwd": "",
         "cmdline": "C:\\py\\python.exe -m http.server 8000", "started_at": "2026-10-27T00:00:00Z", "cpu": 1.5,
         "memory_mb": 30, "ports": [8000], "hub": False, "depth": 0},
        {"pid": 11, "ppid": 10, "name": "node.exe", "exe": "C:\\node\\node.exe", "cwd": "web",
         "cmdline": '"C:\\node\\node.exe" vite', "started_at": "2026-10-27T00:00:01Z", "cpu": 0.0,
         "memory_mb": 2048, "ports": [5173, 8000], "hub": False, "depth": 1},
        {"pid": 99, "ppid": 1, "name": "python.exe", "exe": "C:\\py\\python.exe", "cwd": "",
         "cmdline": "C:\\py\\python.exe -m hub", "started_at": "2026-10-27T00:00:02Z", "cpu": 0.2,
         "memory_mb": 80, "ports": [8765], "hub": True, "depth": 0},
    ]
}  # fmt: skip
GIT = {
    "repo": True,
    "branch": "main",
    "ahead": 1,
    "behind": 0,
    "changes": [
        {"path": "src/app.py", "index": " ", "worktree": "M"},
        {"path": "notes.txt", "index": "?", "worktree": "?"},
        {"path": "old.py", "index": "D", "worktree": " "},
    ],
    "log": [{"sha": "abc1234", "author": "Test", "when": "2 hours ago", "subject": "first"}],
}


class FakeClient:
    token = "t"
    base_url = "http://127.0.0.1:8765"
    events_url = "ws://127.0.0.1:1/v1/events"

    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.deleted = []
        self.over_budget = None
        self.renamed = []
        self.saved_budgets = []
        self.deleted_budgets = []
        self.secret_calls = []
        self.key = False
        self.project_list = [dict(PROJECT)]
        self.trees = copy.deepcopy(TREE)
        self.files = dict(FILES)
        self.tree_calls = []
        self.stopped = []
        self.term_list = [dict(TERMINAL)]
        self.inputs = []
        self.resized = []
        self.term_stopped = []
        self.term_removed = []
        self.file_calls = []
        self.updates = []
        self.searches = []
        self.symbol_calls = []
        self.log = []
        self.histories = {"ct": TOOL_HISTORY}
        self.note_list = []
        self.link_list = []
        self.card_state = {"confirmed": None, "draft": None, "versions": [], "missing": [], "drafting": False}

    def models(self):
        return MODELS

    def conversations(self):
        recent = datetime.now(UTC).isoformat().replace("+00:00", "Z")  # "now" is today even at midnight
        old = (NOW - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        return {
            "conversations": [
                {"id": "c1", "title": "Первый", "updated_at": recent, "last_model_id": "anthropic/claude-opus-5-5"},
                {"id": "c0", "title": "Старый", "updated_at": old, "last_model_id": "llama-swap/coder"},
            ],
            "busy": [],
        }

    def conversation(self, cid):
        return self.histories.get(cid, HISTORY)

    def set_context(self, cid, **settings):
        self.log.append(("context", cid, settings))
        return {"map": True, "live": True, "pinned": [], **settings}

    def set_chat_projects(self, cid, project_ids):
        self.log.append(("projects", cid, list(project_ids)))
        return list(project_ids)

    def status(self):
        return STATUS

    def usage(self):
        return {"today_cost_usd": 0.0003, "total_cost_usd": 0.5}

    def budgets(self):
        return [BUDGET]

    def set_budget(self, **body):
        if body.get("soft_usd") and body.get("hard_usd") and body["soft_usd"] > body["hard_usd"]:
            raise HubError("the soft limit (warning) must not be above the hard limit")
        self.saved_budgets.append(body)
        return body

    def delete_budget(self, budget_id):
        self.deleted_budgets.append(budget_id)

    def secrets(self):
        return [{"name": "anthropic_api_key", "label": "x", "set": self.key}]

    def set_secret(self, name, value):
        self.secret_calls.append(("set", name, value))
        self.key = True
        return {"name": name, "set": True, "catalog": {"synced": 2}}

    def delete_secret(self, name):
        self.secret_calls.append(("delete", name))
        self.key = False
        return {"name": name, "set": False}

    def rename(self, cid, title):
        self.renamed.append((cid, title))
        return {"id": cid, "title": title}

    def create_conversation(self):
        return "c2"

    def send(self, cid, text, model_id, effort, confirm_over_budget=False):
        if self.over_budget is not None and not (confirm_over_budget and self.over_budget):
            data = {"code": "budget_exceeded", "budgets": [REACHED], "can_confirm": self.over_budget}
            raise HubError("budget reached", code="budget_exceeded", data=data)
        self.sent.append((cid, text, model_id, effort, confirm_over_budget))
        self.log.append(("send", cid, text))
        return {}

    def cancel(self, cid):
        self.cancelled.append(cid)
        return True

    def delete_conversation(self, cid):
        self.deleted.append(cid)

    # --- projects ---

    def projects(self):
        return list(self.project_list)

    def add_project(self, path, name=""):
        if any(p["path"] == path for p in self.project_list):
            raise HubError(f"already a project: {path}")
        project = {"id": "p2", "name": path.rstrip("/").rsplit("/", 1)[-1], "path": path, "exists": True}
        self.project_list.append(project)
        return project

    def update_project(self, pid, **changes):
        self.updates.append((pid, changes))
        project = next(p for p in self.project_list if p["id"] == pid)
        project.update(changes)
        return dict(project)

    def remove_project(self, pid):
        self.project_list = [p for p in self.project_list if p["id"] != pid]

    def tree(self, pid, path=""):
        self.tree_calls.append(path)
        return self.trees[path]

    def card(self, pid):
        return dict(self.card_state)

    def notes(self, pid):
        return [dict(n) for n in self.note_list]

    def links(self):
        return [dict(link) for link in self.link_list]

    def scan_links(self):
        evidence = "LocalChat/hub/settings.py:12 обращается к :8080; в папке llama-swap его слушает llama-swap.exe"
        found = {"id": 7, "a_id": "p1", "b_id": "p2", "a_name": "LocalChat", "b_name": "llama-swap", "kind": "uses",
                 "description": "", "status": "proposed", "evidence": [evidence]}  # fmt: skip
        self.link_list = [found]
        return {"added": 1, "links": self.links()}

    def set_link(self, link_id, **changes):
        self.log.append(("link", link_id, changes))
        link = next(x for x in self.link_list if x["id"] == link_id)
        link.update(changes)
        return dict(link)

    def delete_link(self, link_id):
        self.link_list = [x for x in self.link_list if x["id"] != link_id]

    def add_note(self, pid, text):
        note = {"id": len(self.note_list) + 1, "project_id": pid, "text": text}
        self.note_list.append(note)
        return note

    def update_note(self, note_id, text):
        note = next(n for n in self.note_list if n["id"] == note_id)
        note["text"] = text
        return dict(note)

    def delete_note(self, note_id):
        self.note_list = [n for n in self.note_list if n["id"] != note_id]

    def draft_card(self, pid, model_id, *, update=False):
        self.log.append(("draft", pid, model_id) + (("update",) if update else ()))
        self.card_state = {**self.card_state, "drafting": True}
        return {"drafting": True}

    def confirm_card(self, pid, text):
        version = len(self.card_state["versions"]) + 1
        confirmed = {"version": version, "text": text, "model_id": None, "created_at": "2026-10-27T03:00:00Z"}
        self.card_state = {**self.card_state, "confirmed": confirmed, "draft": None, "drafting": False,
                           "versions": [confirmed, *self.card_state["versions"]]}  # fmt: skip
        self.log.append(("confirm", pid, text))
        return dict(self.card_state)

    def restore_card(self, pid, version):
        self.log.append(("restore", pid, version))
        return dict(self.card_state)

    def drop_card_draft(self, pid):
        self.card_state = {**self.card_state, "draft": None}
        self.log.append(("drop", pid))

    def search(self, pid, text, *, regex=False, case="smart", limit=500):
        self.searches.append((text, regex, case))
        found = []
        for path, content in sorted(self.files.items()):
            matches = [
                {"line": n, "text": line, "ranges": [[line.find(text), line.find(text) + len(text)]]}
                for n, line in enumerate(content.splitlines(), 1)
                if text in line
            ]
            if matches:
                found.append({"path": path, "matches": matches})
        return {"engine": "fake", "files": found, "matches": sum(len(f["matches"]) for f in found), "truncated": False}

    def find_files(self, pid, part, limit=20):
        return [path for path in sorted(self.files) if part.casefold() in path.casefold()][:limit]

    def symbols(self, pid, *, q="", path="", kind="", limit=200):
        self.symbol_calls.append((q, path))
        defs = [
            {"path": "src/app.py", "name": "App", "kind": "class", "line": 1, "end_line": 2, "container": "",
             "signature": "class App:"},
            {"path": "src/app.py", "name": "run", "kind": "method", "line": 2, "end_line": 2, "container": "App",
             "signature": "def run(self): print('hi')"},
        ]  # fmt: skip
        if path:
            return {"path": path, "symbols": [{k: v for k, v in d.items() if k != "path"} for d in defs]}
        found = [d for d in defs if q.casefold() in d["name"].casefold()]
        return {"symbols": found, "total": len(found), "complete": q != "partial"}

    def file(self, pid, path):
        self.file_calls.append(path)
        if path == "logo.png":
            return {"path": path, "size": 2048, "truncated": False, "binary": True, "text": "", "encoding": ""}
        if path not in self.files:
            raise HubError(f"not a file: {path}")
        text = self.files[path]
        return {"path": path, "size": len(text.encode()), "truncated": False, "binary": False, "text": text,
                "encoding": "utf-8"}  # fmt: skip

    def terminals(self, project_id=None):
        return [dict(t) for t in self.term_list if project_id in (None, t["project_id"])]

    def start_terminal(self, project_id, *, cols, rows, path="", command=None):
        info = {**TERMINAL, "id": f"t{len(self.term_list) + 1}", "project_id": project_id, "cols": cols, "rows": rows,
                "created_at": "2026-10-27T01:00:00.000Z"}  # fmt: skip
        self.term_list.append(info)
        return dict(info)

    def terminal_output(self, terminal_id):
        return {"data": TAIL, "end": len(TAIL), "running": True}

    def terminal_input(self, terminal_id, data):
        self.inputs.append((terminal_id, data))

    def resize_terminal(self, terminal_id, cols, rows):
        self.resized.append((terminal_id, cols, rows))

    def stop_terminal(self, terminal_id):
        self.term_stopped.append(terminal_id)

    def remove_terminal(self, terminal_id):
        self.term_removed.append(terminal_id)
        self.term_list = [t for t in self.term_list if t["id"] != terminal_id]

    def feed(self, *, limit=100, before=None, project_id="", types=""):
        events = [e for e in FEED if before is None or e["id"] < before]
        events = [e for e in events if not project_id or e["project_id"] == project_id]
        return {"events": events[:limit], "more": len(events) > limit}

    def processes(self):
        return copy.deepcopy(PROCESSES)

    def stop_process(self, pid):
        self.stopped.append(pid)
        return {"stopped": True, "pids": [pid]}

    def git(self, pid, log=20):
        return GIT

    def diff(self, pid, path="", staged=False):
        text = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1,2 @@\n print('hi')\n+print('new')\n"
        return {"path": path, "staged": staged, "diff": text, "truncated": False}

    def close(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met"
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


@pytest.fixture
def app(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat)
    theme = Theme("dark")
    controller = Controller(data_paths().ensure(), settings, theme)
    controller.attach(FakeClient(), start_events=False)
    wait_until(lambda: not controller.bg.busy)
    yield controller
    controller.shutdown()
    theme.setMode("dark")


def opened(app):
    app.openChat("c1")
    wait_until(lambda: len(app.messages.rows) == 2)
    return app.messages.rows


def send(app, event_type, payload):
    """An event from the hub, through the controller's entry point, so its routing is tested too."""
    app.on_event({"type": event_type, "payload": payload})


def event(event_type, **payload):
    return {"type": event_type, "payload": {"conversation_id": "c1", **payload}}


# --- rows (no Qt) ---


def test_rows_split_answers_and_group_chats():
    with_code = rows.segments("текст\n\n```python\nx = 1\n```")
    assert [s["kind"] for s in with_code] == ["prose", "code"]
    assert with_code[1]["lang"] == "python" and with_code[1]["code"] == "x = 1"
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert rows.day_section("2026-10-27T09:00:00.000Z", now) == "Сегодня"
    assert rows.day_section("2026-10-26T09:00:00.000Z", now) == "Вчера"
    assert rows.day_section("2026-10-22T09:00:00.000Z", now) == "Последние 7 дней"
    assert rows.day_section("2026-09-01T09:00:00.000Z", now) == "Ранее"
    assert qml_colour("rgba(255, 255, 255, 0.08)") == "#14ffffff"


def test_picker_lists_local_first_and_marks_what_is_in_vram():
    items = rows.picker_items(MODELS, {"coder"}, "anthropic/claude-opus-5-5")
    assert [i.get("section") or i["name"] for i in items] == [
        "Локальные · на этой видеокарте",
        "Coder",
        "Облако",
        "Claude Opus 5.5",
    ]
    assert items[1]["loaded"] and items[1]["note"] == "загружена в видеопамять"
    assert items[3]["selected"] and items[3]["note"] == "$4 / $20 за 1M · контекст 1000K"


# --- the controller ---


def test_models_chats_status_and_session(app):
    assert app.modelName == "Coder" and not app.modelCloud  # local first by default
    assert app.modelCountText == "2 модели"
    chats = app.chats
    assert [(c["title"], c["section"], c["cloud"]) for c in chats] == [
        ("Первый", "Сегодня", True),
        ("Старый", "Ранее", False),
    ]
    assert app.status["vramText"] == "8.0/16.0 ГБ" and app.status["local"] == "coder · готова"
    assert app.hubState == "up" and app.hubPort == ":8765"
    assert app.sessionText == "Сессия: 0 ток. · $0"


def test_history_title_meta_and_the_chats_model(app):
    user, answer = opened(app)
    assert user["role"] == "user" and user["text"] == "привет"
    assert answer["author"] == "Claude Opus 5.5" and not answer["local"]
    assert answer["thinking"] == {"text": "подумать", "seconds": "2.5 с", "running": False, "open": False}
    assert [s["kind"] for s in answer["segments"]] == ["prose", "code", "prose"]
    assert 'border="1"' in answer["segments"][2]["html"]  # the table has its grid
    assert answer["footer"] == "30.0 т/с · 12 ток. · $0.0003"
    assert app.currentTitle == "Первый"
    assert app.currentMeta == "2 сообщения · Claude Opus 5.5 · $0.0003"
    assert app.modelId == "anthropic/claude-opus-5-5"  # the chat continues with its model
    app.toggleThinking(1)
    assert app.messages.rows[1]["thinking"]["open"] is True


def test_a_streamed_answer(app):
    opened(app)
    on = app.on_event
    on(event("chat.run_started", model_id="llama-swap/coder"))
    assert app.busyNow
    live = app.messages.find("live")
    assert live == 2 and app.messages.rows[live]["streaming"]
    on(event("chat.loading", text="llama-swap loading model: coder\n"))
    wait_until(lambda: app.messages.rows[live]["notice"].startswith("Загрузка модели"))
    on(event("chat.delta", kind="reasoning", text="хм"))
    wait_until(lambda: (app.messages.rows[live]["thinking"] or {}).get("running") is True)
    on(event("chat.delta", kind="content", text="Ответ `код`"))
    wait_until(lambda: app.messages.rows[live]["segments"])
    assert app.messages.rows[live]["thinking"]["running"] is False
    final = {
        "id": 3,
        "role": "assistant",
        "model_id": "llama-swap/coder",
        "content": [{"type": "text", "text": "Ответ"}],
    }
    on(event("chat.message", message=final))
    usage = {"input_tokens": 40, "output_tokens": 2}
    on(event("chat.done", message_id=3, usage=usage, stats={"tok_s": 99.0}, cost_usd=0.0, cancelled=False))
    assert app.messages.find("live") == -1 and app.messages.find("m3") == 2
    assert app.messages.rows[2]["footer"] == "99.0 т/с · 2 ток."
    assert app.currentMeta == "3 сообщения · Coder · $0.0003"  # the subtitle follows the conversation
    assert not app.busyNow
    assert app.sessionText == "Сессия: 42 ток. · $0"


def test_refusal_is_a_row_with_its_cost(app):
    opened(app)
    app.on_event(event("chat.run_started", model_id="x"))
    payload = {"detail": "Claude declined.", "refusal": True, "cost_usd": 0.002, "usage": {"output_tokens": 5}}
    app.on_event(event("chat.error", **payload))
    error = app.messages.rows[-1]
    assert error["role"] == "error" and "$0.0020" in error["text"]
    assert app.messages.find("live") == -1
    assert app.sessionText == "Сессия: 5 ток. · $0.0020"


def test_budget_question_then_confirmed_send(app):
    app.client.over_budget = True
    app.chooseModel("anthropic/claude-opus-5-5")
    app.send("дорогой вопрос")
    wait_until(lambda: bool(app.question))
    assert app.question["title"] == "Бюджет исчерпан"
    assert "$10.40 из $10.00" in app.question["text"]
    app.answer(True)
    wait_until(lambda: app.client.sent)
    assert app.client.sent == [("c2", "дорогой вопрос", "anthropic/claude-opus-5-5", "medium", True)]
    assert app.question == {}


def test_declining_the_budget_restores_the_draft_and_removes_the_new_chat(app):
    drafts = []
    app.draftRestored.connect(drafts.append)
    app.client.over_budget = True
    app.chooseModel("anthropic/claude-opus-5-5")
    app.send("дорогой вопрос")
    wait_until(lambda: bool(app.question))
    app.answer(False)
    wait_until(lambda: app.client.deleted == ["c2"])
    assert drafts == ["дорогой вопрос"] and app.client.sent == []


def test_theme_switch_rerenders_the_html(app):
    opened(app)
    app.setTheme("light")
    assert palette.current() is palette.LIGHT
    assert palette.LIGHT.text_soft in app.messages.rows[1]["segments"][0]["html"]
    assert app.theme.p["window"] == palette.LIGHT.window
    assert app.settings.value("ui/theme") == "light"


def test_copy_puts_text_on_the_clipboard(app):
    app.copy("print(1)")
    assert QApplication.clipboard().text() == "print(1)"
    assert app.flashText == "Скопировано"


# --- the real QML ---


def test_the_qml_window_shows_history_and_a_stream_without_warnings(qapp, tmp_path):
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        app.toggleThinking(1)
        app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
        app.on_event(event("chat.delta", kind="reasoning", text="хм"))
        app.on_event(event("chat.delta", kind="content", text="Ответ\n\n```js\nlet a = 1\n```"))
        wait_until(lambda: app.messages.rows[-1]["segments"])
        app.setTheme("light")
        for _ in range(30):
            QApplication.processEvents()
        image = quick.window.grabWindow()
        assert not image.isNull() and image.width() > 0
        from PySide6.QtCore import QObject

        conversation = next(
            o for o in quick.window.findChildren(QObject) if o.metaObject().className().startswith("ChatView")
        )
        # history with code, a table and an answer being streamed: well over one screen line per message
        assert conversation.property("count") == 3
        assert conversation.property("contentHeight") > 400
        assert quick.warnings == []
    finally:
        quick.close()
        quick.theme.setMode("dark")


# --- step 6.2: the palette, budgets, the key, chat actions ---


def test_palette_items_and_highlight():
    chats = [{"id": "c1", "title": "Бюджет <на> месяц", "section": "Сегодня", "cloud": True}]
    everything = commands.palette_items("", chats=chats, models=MODELS, budgets=[BUDGET], has_current=False)
    sections = [i["section"] for i in everything if i.get("section")]
    assert sections == ["Действия", "Недавние чаты"]
    ids = [i["id"] for i in everything]
    assert "action:new" in ids and "action:rename" not in ids  # chat actions need an open chat
    found = commands.palette_items("бюдж", chats=chats, models=MODELS, budgets=[BUDGET], has_current=True)
    names = {i["id"]: i["name"] for i in found if i.get("id")}
    assert names["action:budgets"] == "<b>Бюдж</b>еты…"
    assert names["chat:c1"] == "<b>Бюдж</b>ет &lt;на&gt; месяц"  # the rest is escaped
    assert "budget:1" in names
    models = commands.palette_items("coder", chats=[], models=MODELS, budgets=[], has_current=False)
    assert [i["id"] for i in models if i.get("id")] == ["model:llama-swap/coder"]
    assert commands.palette_items("zzz", chats=chats, models=MODELS, budgets=[BUDGET], has_current=True) == []


def test_budget_cards_and_scopes():
    (card,) = commands.budget_items([BUDGET])
    assert (card["title"], card["spent"], card["limit"]) == ("Все платные модели за месяц", "$1.25", "из $10.00")
    assert card["ratio"] == 0.125 and card["softAt"] == 0.5
    assert card["note"] == "Предупредить с $5.00 · после лимита — спрашивать"
    assert (card["scopeKey"], card["soft"], card["hard"]) == ("total:", "5", "10")
    scopes = commands.budget_scopes(MODELS, [BUDGET])
    assert [s["id"] for s in scopes] == ["total:", "provider:anthropic"]


def test_commands_open_panels_and_act(app):
    asked = []
    app.uiRequested.connect(asked.append)
    app.runCommand("action:budgets")
    app.runCommand("action:key")
    app.runCommand("model:anthropic/claude-opus-5-5")
    assert asked == ["budgets", "settings"] and app.modelId == "anthropic/claude-opus-5-5"
    app.runCommand("chat:c1")
    wait_until(lambda: len(app.messages.rows) == 2)
    app.runCommand("action:theme:light")
    assert app.theme.mode == "light"
    app.runCommand("action:rename")
    assert asked[-1] == "rename"


def test_budgets_are_saved_checked_and_deleted(app):
    errors = []
    app.budgetError.connect(errors.append)
    assert app.budgetItems[0]["title"] == "Все платные модели за месяц"
    app.saveBudget("provider:anthropic", "day", "1,5", "3", "block")
    wait_until(lambda: app.client.saved_budgets)
    assert app.client.saved_budgets[0] == {
        "scope": "provider",
        "provider": "anthropic",
        "period": "day",
        "soft_usd": 1.5,
        "hard_usd": 3.0,
        "hard_action": "block",
    }
    app.saveBudget("total:", "month", "много", "", "ask")
    assert errors == ["Суммы — числа в долларах, например 5 или 2.50"]
    app.saveBudget("total:", "month", "9", "3", "ask")
    wait_until(lambda: len(errors) == 2)
    assert "soft limit" in errors[1]
    app.deleteBudget(1)
    wait_until(lambda: app.client.deleted_budgets == [1])


def test_the_anthropic_key(app):
    assert app.keySet is False
    app.setAnthropicKey("  sk-ant-test  ")
    wait_until(lambda: app.keySet)
    assert app.client.secret_calls == [("set", "anthropic_api_key", "sk-ant-test")]
    app.deleteAnthropicKey()
    assert app.question["title"] == "Удалить ключ Anthropic?"
    app.answer(True)
    wait_until(lambda: not app.keySet)
    assert app.client.secret_calls[-1] == ("delete", "anthropic_api_key")


def test_rename_and_delete_a_chat(app):
    opened(app)
    app.renameChat("", "  Новое имя  ")  # "" - the open chat
    wait_until(lambda: app.client.renamed)
    assert app.client.renamed == [("c1", "Новое имя")]
    assert app.chatTitle("c1") == "Первый"
    app.askDeleteChat("c1")
    assert app.question["title"] == "Удалить чат «Первый»?"
    app.answer(True)
    wait_until(lambda: app.client.deleted == ["c1"])
    wait_until(lambda: app.current is None)  # the open chat was the deleted one


def test_the_palette_sheets_and_rename_dialog_open_without_warnings(qapp):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        for name in ("palette", "budgetsSheet", "settingsSheet", "renameDialog"):
            popup = quick.window.findChild(QObject, name)
            assert popup is not None, name
            QMetaObject.invokeMethod(popup, "open")
            wait_until(lambda p=popup: p.property("opened"))  # after the enter animation
            if name == "palette":
                items = popup.property("items")  # a QJSValue or already a list, depending on how it was set
                assert len(items.toVariant() if hasattr(items, "toVariant") else items) > 4
            QMetaObject.invokeMethod(popup, "close")
            for _ in range(20):
                QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()
        quick.theme.setMode("dark")


# --- stage 2: projects ---


def test_project_rows():
    marks = project_rows.marks(GIT["changes"])
    assert marks == {"src/app.py": "M", "src": "•", "notes.txt": "U", "old.py": "D"}
    assert project_rows.mark({"index": "R", "worktree": " "}) == "R"
    assert project_rows.mark({"index": "U", "worktree": "U"}) == "!"
    assert project_rows.git_summary(GIT) == "main · ↑1 ↓0 · 3 изменения"
    assert project_rows.git_summary({"repo": False}) == "не git-репозиторий"
    assert [(c["name"], c["folder"], c["label"]) for c in project_rows.change_rows(GIT["changes"])] == [
        ("app.py", "src", "изменён"),
        ("notes.txt", "", "новый"),
        ("old.py", "", "удалён"),
    ]
    rows_ = project_rows.tree_rows(TREE[""], 0, marks)
    assert [(r["name"], r["depth"], r["status"]) for r in rows_] == [
        ("src", 0, "•"),
        ("logo.png", 0, ""),
        ("README.md", 0, ""),
    ]
    code = project_rows.code_view("".join(f"x = {n}\n" for n in range(10)), "a.py")
    assert code["rich"] and code["lines"] == 10 and "<span" in code["code"]
    assert " 9\n10" in code["numbers"]  # padded: the numbers line up on the right
    plain = project_rows.code_view("<b>не html</b>", "notes.unknownext")
    assert not plain["rich"] and plain["code"] == "<b>не html</b>" and plain["numbers"] == "1"
    big = project_rows.code_view("x = 1\n" * 60_000, "big.py")
    assert not big["rich"]  # too big to colour
    diff = project_rows.diff_view("@@ -1 +1 @@\n-old <x>\n+new\n same\n")
    assert f'<span style="color:{palette.DARK.ok};">+new</span>' in diff["code"]
    assert f'<span style="color:{palette.DARK.error};">-old &lt;x&gt;</span>' in diff["code"]
    assert "\n same</pre>" in diff["code"]
    view = project_rows.file_view({"path": "src/app.py", "size": 26, "text": FILES["src/app.py"], "encoding": "utf-8"})
    assert (view["name"], view["folder"], view["linesText"], view["sizeText"]) == ("app.py", "src", "2 строки", "26 Б")


def test_projects_tree_files_and_git(app):
    projects = app.projects
    assert [p["id"] for p in projects.projectList] == ["p1"] and projects.currentId == ""
    projects.openProject("p1")
    wait_until(lambda: len(projects.tree.rows) == 3 and projects.git.get("repo"))
    assert app.settings.value("projects/current") == "p1"
    assert projects.git["summary"] == "main · ↑1 ↓0 · 3 изменения"
    assert projects.tree.rows[0]["status"] == "•"  # a folder with a change inside
    projects.toggleDir(0)
    wait_until(lambda: len(projects.tree.rows) == 5)
    assert [(r["name"], r["depth"], r["status"]) for r in projects.tree.rows[:3]] == [
        ("src", 0, "•"),
        ("deep", 1, ""),
        ("app.py", 1, "M"),
    ]
    projects.toggleDir(1)
    wait_until(lambda: len(projects.tree.rows) == 6)
    assert projects.tree.rows[2]["path"] == "src/deep/x.txt" and projects.tree.rows[2]["depth"] == 2
    projects.refresh()  # open folders stay open
    wait_until(lambda: not app.bg.busy)
    assert len(projects.tree.rows) == 6 and projects.tree.rows[1]["open"]
    projects.toggleDir(0)  # closing a folder closes what is open inside it
    assert [r["name"] for r in projects.tree.rows] == ["src", "logo.png", "README.md"]

    projects.openFile("src/app.py", False)
    wait_until(lambda: projects.file.get("path") == "src/app.py")
    assert projects.file["rich"] and projects.file["hasDiff"] and projects.file["mark"] == "M"
    assert projects.file["linesText"] == "2 строки"
    projects.showDiff(True)
    wait_until(lambda: projects.file.get("diffMode"))
    assert "+print('new')" in projects.file["diff"] and projects.file["diffRich"]
    projects.openFile("logo.png", False)
    wait_until(lambda: projects.file.get("path") == "logo.png")
    assert projects.file["binary"] and not projects.file["hasDiff"]
    projects.openFile("old.py", True)  # deleted: only its diff
    wait_until(lambda: projects.file.get("path") == "old.py")
    assert projects.file["missing"] and projects.file["diffMode"]
    app.setTheme("light")
    assert "<span" in projects.file["diff"]  # coloured again for the new theme
    projects.closeFile()
    assert projects.file == {}


def test_adding_and_removing_projects(app):
    projects = app.projects
    projects.addProject("file:///D:/Work/new")
    wait_until(lambda: projects.currentId == "p2")
    assert app.client.project_list[-1]["path"] == "D:/Work/new"
    assert [p["name"] for p in projects.projectList] == ["LocalChat", "new"]
    projects.addProject("D:/Work/new")
    wait_until(lambda: app.flashError)
    assert "already a project" in app.flashText
    projects.askRemoveProject("p2")
    assert "new" in app.question["title"]
    app.answer(True)
    wait_until(lambda: projects.currentId == "")
    assert [p["id"] for p in projects.projectList] == ["p1"] and projects.tree.rows == []
    projects.openProject("p1")
    wait_until(lambda: projects.tree.rows)
    send(app, "project.removed", {"id": "p1"})  # removed elsewhere
    assert projects.currentId == "" and projects.projectList == []


def test_the_window_subscribes_to_every_event_it_handles():
    """An event type the controller handles but the event thread does not subscribe to never arrives (project
    events were missed this way once): every handled type must match the subscription."""
    import re
    from pathlib import Path

    from app import events
    from hub.events import matches

    handled = set()
    for name in ("controller.py", "projects.py", "terminals.py", "feed_rows.py"):
        source = (Path(events.__file__).parent / "ui" / name).read_text(encoding="utf-8")
        handled |= set(re.findall(r'case "([a-z_]+\.[a-z_.]+)"', source))
        handled |= set(re.findall(r'\| "([a-z_]+\.[a-z_.]+)"', source))
        for group in re.findall(r'kind (?:==|in) (\("[^)]*\)|"[^"]*")', source):
            handled |= {k for k in re.findall(r'"([a-z_]+\.[a-z_.]+)"', group)}
        handled |= {f"{prefix}x" for prefix in re.findall(r'kind\.startswith\("([a-z_]+\.)"\)', source)}
    assert {"chat.delta", "project.files", "project.git", "budget.warning", "system.status", "git.head",
            "port.opened", "terminal.output"} <= handled  # fmt: skip
    missing = sorted(k for k in handled if not matches(k, tuple(events.TOPICS)))
    assert missing == []


def test_row_model_sync_changes_only_what_differs(qapp):
    model = RowModel(roles=TREE_ROLES)
    seen = []
    model.modelReset.connect(lambda: seen.append("reset"))
    model.rowsInserted.connect(lambda _p, first, last: seen.append(("insert", first, last)))
    model.rowsRemoved.connect(lambda _p, first, last: seen.append(("remove", first, last)))
    model.dataChanged.connect(lambda top, _b, _r: seen.append(("change", top.row())))

    def row(key, status=""):
        return {"key": key, "name": key, "path": key, "depth": 0, "dir": False, "open": False, "status": status}

    model.sync([row("a"), row("b"), row("c")])
    assert seen == [("insert", 0, 2)]
    seen.clear()
    model.sync([row("a"), row("new"), row("b"), row("c", "M")])  # one inserted, one changed in place
    assert seen == [("insert", 1, 1), ("change", 3)]
    seen.clear()
    model.sync([row("a"), row("c", "M")])
    assert seen == [("remove", 1, 2)]
    assert [r["key"] for r in model.rows] == ["a", "c"] and "reset" not in seen


def test_projects_follow_the_hubs_events(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: len(projects.tree.rows) == 3 and projects.git.get("repo"))
    projects.toggleDir(0)
    wait_until(lambda: len(projects.tree.rows) == 5)
    projects.openFile("src/app.py", False)
    wait_until(lambda: projects.file.get("path") == "src/app.py")

    # a new file in an open folder, and the open file changed on disk
    client.trees["src"].append({"name": "new.py", "path": "src/new.py", "dir": False, "size": 3})
    client.files["src/app.py"] = "print('changed')\n"
    send(app, "project.files", {"project_id": "p1", "changed": ["src/new.py", "src/app.py"],
                                        "folders": ["src"], "more": False})  # fmt: skip
    wait_until(lambda: any(r["path"] == "src/new.py" for r in projects.tree.rows))
    wait_until(lambda: "changed" in projects.file["code"])
    assert projects.file["linesText"] == "1 строка"

    # a change in a closed folder or in another project costs nothing
    calls = len(client.tree_calls)
    send(app, "project.files", {"project_id": "p1", "changed": ["src/deep/x.txt"], "folders": ["src/deep"]})
    send(app, "project.files", {"project_id": "p9", "changed": [], "folders": [""]})
    wait_until(lambda: not app.bg.busy)
    assert len(client.tree_calls) == calls

    # git says the work was committed: the marks go, the open file has no diff any more
    send(app, "project.git", {**GIT, "project_id": "p1", "changes": [], "log": []})
    assert projects.git["summary"] == "main · ↑1 ↓0 · без изменений"
    assert all(r["status"] == "" for r in projects.tree.rows)
    assert projects.file["hasDiff"] is False and projects.file["mark"] == ""

    # the open file is deleted: shown as deleted, no error
    del client.files["src/app.py"]
    send(app, "project.files", {"project_id": "p1", "changed": ["src/app.py"], "folders": ["src"]})
    wait_until(lambda: projects.file.get("missing"))
    assert projects.file["path"] == "src/app.py" and not app.flashError


def test_project_processes(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: len(projects.processes) == 3)
    rows_ = projects.processes
    assert [(r["name"], r["depth"], r["args"]) for r in rows_[:2]] == [
        ("python.exe", 0, "-m http.server 8000"),
        ("node.exe", 1, "vite"),
    ]
    assert rows_[0]["cpu"] == "1.5%" and rows_[1]["cpu"] == "" and rows_[1]["memory"] == "2.0 ГБ"
    assert rows_[1]["folder"] == "web" and rows_[2]["hub"]
    assert projects.processesSummary == "3 процесса · порты 5173, 8000, 8765"

    projects.askStopProcess(99)  # the hub itself: not offered
    assert app.question == {}
    projects.askStopProcess(11)
    assert "node.exe" in app.question["title"]
    app.answer(True)
    wait_until(lambda: client.stopped == [11])

    # the hub's scan arrives as an event; another project's processes do not touch this one
    send(app, "processes.changed", {"projects": {"p1": PROCESSES["p1"][:1], "p9": PROCESSES["p1"]}})
    assert len(projects.processes) == 1 and projects.processesSummary == "1 процесс · порт 8000"
    send(app, "processes.changed", {"projects": {}})
    assert projects.processes == [] and projects.processesSummary == ""


def test_search_in_a_project(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.tree.rows)
    projects.search("pri", False, False)
    projects.search("print", False, False)  # typing: only the last one goes out
    assert projects.searchActive and projects.searchSummary == "поиск…"
    wait_until(lambda: projects.searchRows)
    assert client.searches == [("print", False, "smart")]
    assert [(r["kind"], r["path"], r["line"]) for r in projects.searchRows] == [
        ("file", "src/app.py", 0),
        ("match", "src/app.py", 1),
        ("match", "src/app.py", 2),
    ]
    assert projects.searchRows[0]["count"] == 2
    assert projects.searchRows[1]["html"].startswith('<span style="background-color:')  # the match is marked
    assert projects.searchSummary == "2 совпадения в 1 файле"
    projects.search("print", True, True)
    wait_until(lambda: len(client.searches) == 2)
    assert client.searches[-1] == ("print", True, "sensitive")

    projects.openFileAt("src/app.py", 2)
    wait_until(lambda: projects.file.get("gotoSeq"))
    assert projects.file["gotoLine"] == 2 and projects.file["gotoOffset"] == len("print('hi')\n")
    calls, seq = len(client.file_calls), projects.file["gotoSeq"]
    projects.openFileAt("src/app.py", 1)  # the same file: no reload, just another place
    assert projects.file["gotoOffset"] == 0 and projects.file["gotoSeq"] == seq + 1
    assert len(client.file_calls) == calls
    projects.openFile("README.md", False)  # from the tree: from the top
    wait_until(lambda: projects.file.get("path") == "README.md")
    assert projects.file["gotoOffset"] == -1

    projects.search("nothing-here", False, False)
    wait_until(lambda: projects.searchSummary == "ничего не найдено")
    projects.clearSearch()
    assert not projects.searchActive and projects.searchRows == []


def test_symbols_in_the_window(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.tree.rows)
    projects.search("ru", False, False)
    wait_until(lambda: client.searches)
    projects.setSymbolSearch(True)  # the same query again, now in the symbol index
    assert projects.symbolSearch
    wait_until(lambda: client.symbol_calls)
    wait_until(lambda: projects.searchRows and projects.searchRows[0]["kind"] == "symbol")
    assert client.symbol_calls == [("ru", "")]
    row = projects.searchRows[0]
    assert (row["name"], row["container"], row["folder"], row["badge"], row["line"]) == (
        "run",
        "App",
        "src/app.py:2",
        "ƒ",
        2,
    )
    assert row["tip"] == "метод: def run(self): print('hi')"
    assert projects.searchSummary == "1 определение"
    projects.search("partial", False, False)
    wait_until(lambda: projects.searchSummary == "определений не найдено · индекс ещё строится")
    projects.setSymbolSearch(False)
    wait_until(lambda: client.searches[-1] == ("partial", False, "smart"))

    from app.ui import project_rows

    many = {"symbols": [{"path": "a.py", "name": "x", "kind": "function", "line": 1, "end_line": 1, "container": "",
                         "signature": "def x():"}], "total": 120, "complete": True}  # fmt: skip
    assert project_rows.search_summary(many) == "120 определений (показаны первые)"

    # the outline of the open file
    projects.openFile("README.md", False)
    wait_until(lambda: projects.file.get("path") == "README.md")
    assert not projects.file["outline"]  # not code
    projects.openFile("src/app.py", False)
    wait_until(lambda: projects.file.get("path") == "src/app.py")
    assert projects.file["outline"] and projects.outline["rows"] == []
    projects.loadOutline()
    assert projects.outline["state"] == "loading"
    wait_until(lambda: projects.outline["state"] == "ready")
    assert [(r["name"], r["depth"], r["badge"], r["line"]) for r in projects.outline["rows"]] == [
        ("App", 0, "C", 1),
        ("run", 1, "ƒ", 2),
    ]
    assert client.symbol_calls[-1] == ("", "src/app.py")
    projects.openFile("README.md", False)
    wait_until(lambda: projects.file.get("path") == "README.md")
    assert projects.outline["rows"] == []  # it belonged to the other file


def test_feed_rows():
    from datetime import datetime

    from app.ui import feed_rows

    now = datetime.fromisoformat("2026-10-27T12:00:00+00:00")
    rows_ = feed_rows.feed_rows(FEED, projects={"p1": "LocalChat"}, chats={"c1": "Первый"}, now=now)
    assert [r.get("section") or r["title"] for r in rows_] == [
        "Сегодня",
        "Открыт порт :8000 — python.exe",
        "Коммит в main: fix",
        "Ответ в чате «Первый»",
        "20 октября",
        "Терминал «npm test» завершился с кодом 2",
    ]  # chat.message is plumbing, not news
    assert rows_[1]["project"] == "LocalChat" and rows_[1]["detail"] == "-m http.server 8000"
    assert rows_[3]["detail"] == "1.2K ток. ответа · $0.01" and rows_[5]["level"] == "error"
    git_only = feed_rows.feed_rows(FEED, projects={}, chats={}, group="git", now=now)
    assert [r.get("section") or r["title"] for r in git_only] == ["Сегодня", "Коммит в main: fix"]
    assert feed_rows.day_label("2025-01-03T10:00:00Z", now) == "3 января 2025"
    assert feed_rows.describe({"type": "budget.changed", "payload": {}}, projects={}, chats={})["group"] == "budgets"
    stopped = {"type": "terminal.exited", "payload": {"title": "dev", "stopped": True, "exit_code": 1}}
    assert feed_rows.describe(stopped, projects={}, chats={})["title"] == "Терминал «dev» остановлен"


def test_the_feed(app):
    feed = app.feed
    feed.load()
    wait_until(lambda: feed.items)
    assert [r["id"] for r in feed.items if not r.get("section")] == [9, 7, 6, 5]
    feed.setGroup("processes")
    assert [r["id"] for r in feed.items if not r.get("section")] == [9]
    feed.setGroup("all")
    # a stored event that comes live joins at the top; a live-only one does not
    app.on_event({"id": 10, "ts": "2026-10-27T01:06:00.000Z", "type": "port.closed", "project_id": "p1",
                  "payload": {"project_id": "p1", "port": 8000, "name": "python.exe"}})  # fmt: skip
    app.on_event({"id": None, "type": "port.closed", "payload": {"port": 1}})
    assert [r["id"] for r in feed.items if not r.get("section")][:2] == [10, 9]
    # the open project's latest events, for its overview
    app.projects.openProject("p1")
    wait_until(lambda: feed.recent)
    assert [r["id"] for r in feed.recent] == [9, 7, 5]
    app.on_event({"id": 11, "ts": "2026-10-27T01:07:00.000Z", "type": "terminal.started", "project_id": "p1",
                  "payload": {"terminal": {"title": "PowerShell", "cwd": "D:/x"}}})  # fmt: skip
    assert [r["id"] for r in feed.recent][:2] == [11, 9]
    assert feed.items[1]["title"] == "Запущен терминал: PowerShell"


def test_project_settings(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.currentId == "p1")
    assert not projects.currentCloud and projects.currentDescription == ""
    projects.saveSettings("  Мой хаб ", "бэкенд   и\nокно", True)
    wait_until(lambda: client.updates)
    assert client.updates == [("p1", {"name": "Мой хаб", "description": "бэкенд и окно", "cloud_ok": True})]
    wait_until(lambda: projects.currentCloud)
    assert projects.currentName == "Мой хаб" and projects.currentDescription == "бэкенд и окно"
    rows_ = project_rows.tree_rows([{"name": ".env", "path": ".env", "dir": False, "secret": True}], 0, {})
    assert rows_[0]["secret"] is True


def test_tool_rows():
    assert rows.tool_label("read_file", {"path": "a.py", "start_line": 301}) == "Читаю a.py со строки 301"
    assert rows.tool_label("search_code", {"query": "budget"}) == "Ищу «budget»"
    assert rows.tool_label("list_files", {}) == "Смотрю файлы проекта"
    assert rows.tool_label("terminal_output", {"terminal": "dev"}) == "Читаю терминал «dev»"
    assert rows.tool_label("mystery", None) == "mystery"
    built = rows.rows_for(TOOL_HISTORY["messages"], {m["id"]: m for m in MODELS})
    assert [r["role"] for r in built] == ["user", "assistant", "assistant"]  # the results are not rows
    first, second = built[1]["tools"]
    assert (first["label"], first["state"], first["summary"], first["icon"]) == (
        "Читаю README.md", "ok", "README.md (lines 1-1 of 1)", "file")  # fmt: skip
    assert (second["state"], second["project"]) == ("error", "LocalChat")
    assert built[2]["tools"] == []


def test_chat_projects_and_tool_calls(app):
    client = app.client
    wait_until(lambda: app.projects.items)
    assert [c["id"] for c in app.chatProjectChoices] == ["p1"] and app.chatProjects == []
    # a new chat: the project is attached with its first message, before it goes out
    app.toggleChatProject("p1")
    assert [p["name"] for p in app.chatProjects] == ["LocalChat"] and client.log == []
    app.chooseModel("anthropic/claude-opus-5-5")
    assert app.chatProjects[0]["hidden"] is True  # a cloud model, a local-only project
    app.chooseModel("llama-swap/coder")
    assert app.chatProjects[0]["hidden"] is False
    app.send("что в README?")
    wait_until(lambda: client.sent)
    assert client.log[:2] == [("projects", "c2", ["p1"]), ("send", "c2", "что в README?")]

    # a chat with tool calls: they show under the answer that made them, results joined
    app.openChat("ct")
    wait_until(lambda: len(app.messages.rows) == 3)
    assert app.chatProjects[0]["id"] == "p1" and app.currentMeta.startswith("3 сообщения")
    tools = app.messages.rows[1]["tools"]
    assert [t["state"] for t in tools] == ["ok", "error"]
    app.toggleTool(1, 0)
    assert app.messages.rows[1]["tools"][0]["open"] is True
    app.toggleChatProject("p1")  # an existing chat: the hub hears at once
    wait_until(lambda: client.log[-1] == ("projects", "ct", []))
    send(app, "chat.projects", {"conversation_id": "ct", "projects": ["p1"]})
    assert [p["id"] for p in app.chatProjects] == ["p1"]

    # live: an answer asking for a tool, then its result
    call = {"type": "tool_call", "id": "c9", "name": "git_status", "input": {"project": "LocalChat"}}
    send(app, "chat.message", {"conversation_id": "ct", "message": {"id": 21, "role": "assistant",
         "model_id": "llama-swap/coder", "content": [call]}})  # fmt: skip
    assert app.messages.rows[-1]["tools"][0]["state"] == "running"
    result = {"type": "tool_result", "id": "c9", "name": "git_status", "output": "branch main"}
    send(app, "chat.message", {"conversation_id": "ct", "message": {"id": 22, "role": "tool", "content": [result]}})
    assert app.messages.rows[-1]["tools"][0]["summary"] == "branch main" and len(app.messages.rows) == 4

    app.newChatAbout("p1")
    assert app.mode == "chats" and app.currentId == "" and [p["id"] for p in app.chatProjects] == ["p1"]


def test_the_context_inspector(app):
    client = app.client
    wait_until(lambda: app.projects.items)
    blocks = rows.context_rows(TOOL_HISTORY["messages"])
    assert [b["label"] for b in blocks] == ["Карта проектов", "Проект «LocalChat»", "Сейчас в «LocalChat»",
                                            "Инструменты и правила"]  # fmt: skip
    assert blocks[1]["local"] is True and blocks[0]["tokens"] == 10
    app.openChat("ct")
    wait_until(lambda: app.contextBlocks)
    assert app.contextTokens > 20 and app.contextSettings["map"] is True
    app.toggleContextBlock(1)
    assert app.contextBlocks[1]["open"] is True
    app.setContextLayer("live", False)
    wait_until(lambda: ("context", "ct", {"live": False}) in client.log)
    assert app.contextSettings["live"] is False
    app.pinFile("p1", "src/app.py")
    wait_until(lambda: any(e[0] == "context" and e[2].get("pinned") for e in client.log))
    assert app.contextSettings["pinned"][0]["name"] == "LocalChat/src/app.py"
    app.unpinFile(0)
    wait_until(lambda: client.log[-1] == ("context", "ct", {"pinned": []}))
    app.newChat()
    app.pinFile("p1", "x.py")  # no chat yet: nothing to pin to
    assert app.flashError and app.contextBlocks == []


def test_project_cards(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.currentId == "p1")
    wait_until(lambda: projects.card.get("state") == "none")
    app.chooseModel("anthropic/claude-opus-5-5")  # a cloud model chosen: the draft still goes to a local one
    projects.draftCard()
    wait_until(lambda: any(e[0] == "draft" for e in client.log))
    assert ("draft", "p1", "llama-swap/coder") in client.log and projects.card["state"] == "drafting"

    draft = {"text": "## Назначение\nДемо", "model_id": "llama-swap/coder", "version": 0}
    client.card_state = {**client.card_state, "drafting": False, "draft": draft}
    send(app, "card.drafted", {"project_id": "p1", "name": "LocalChat", "model_id": "llama-swap/coder"})
    wait_until(lambda: projects.card["state"] == "draft")
    assert projects.card["draft"] == "## Назначение\nДемо" and projects.card["draftModel"] == "Coder"

    projects.confirmCard("## Назначение\nДемо, поправлено")
    wait_until(lambda: projects.card["state"] == "confirmed")
    assert projects.card["version"] == 1 and projects.card["segments"][0]["kind"] == "prose"
    send(app, "card.failed", {"project_id": "p1", "detail": "модель молчит"})
    wait_until(lambda: app.flashError)
    assert "модель молчит" in app.flashText
    assert projects.card["changes"] == ""  # the project has not moved since

    # the project moved on: the overview says so and offers an update; the draft shows what it changed (D24)
    changes = {"commits": 23, "new_folders": ["docs"], "gone_folders": [], "dependencies": ["pyproject.toml"],
               "significant": True}  # fmt: skip
    client.card_state = {**client.card_state, "changes": changes}
    projects.load_card()
    wait_until(lambda: projects.card["changes"])
    said = "С подтверждения карточки: 23 коммита; изменились pyproject.toml; новые папки: docs/"
    assert projects.card["changes"] == said
    projects.updateCard()
    wait_until(lambda: client.log[-1][-1] == "update")
    assert client.log[-1] == ("draft", "p1", "llama-swap/coder", "update")
    update = {"text": "## Назначение\nДемо, поправлено\n## Устройство\nновое", "model_id": "llama-swap/coder"}
    client.card_state = {**client.card_state, "drafting": False, "draft": update}
    send(app, "card.drafted", {"project_id": "p1", "name": "LocalChat", "model_id": "llama-swap/coder"})
    wait_until(lambda: projects.card["state"] == "draft")
    assert projects.card["changedSections"] == ["Устройство"]

    from app.ui import feed_rows, project_rows

    assert project_rows.changed_sections("## А\nраз\n## Б\nдва", "## А\nраз, правка\n## В\nтри") == [
        "А", "В", "Б (убран)"
    ]  # fmt: skip
    assert project_rows.card_changes({"commits": 2, "significant": False}) == ""
    from datetime import UTC, datetime

    utc = datetime(2026, 10, 27, 0, 3, tzinfo=UTC)
    assert project_rows.local_time("2026-10-27T00:03:00Z") == utc.astimezone().strftime("%d.%m.%Y %H:%M")
    assert projects.card["versions"][0]["when"] == project_rows.local_time(projects.card["versions"][0]["created_at"])

    payload = {"name": "LocalChat", "version": 3, "restored_from": 1}
    shown = feed_rows.describe({"type": "card.confirmed", "payload": payload}, projects={}, chats={})
    assert shown["title"] == "Карточка «LocalChat» подтверждена, версия 3 (возвращена версия 1)"


def test_project_notes(app):
    projects = app.projects
    projects.openProject("p1")
    wait_until(lambda: projects.currentId == "p1")
    projects.addNote("деплой только из main")
    wait_until(lambda: projects.notes)
    assert projects.notes[0]["text"] == "деплой только из main"
    projects.updateNote(projects.notes[0]["id"], "деплой из main и release")
    wait_until(lambda: projects.notes[0]["text"].endswith("release"))
    projects.addNote("   ")  # nothing to add
    projects.deleteNote(projects.notes[0]["id"])
    wait_until(lambda: projects.notes == [])
    app.client.note_list.append({"id": 9, "project_id": "p1", "text": "из другого окна"})
    send(app, "note.changed", {"project_id": "p1"})
    wait_until(lambda: [n["text"] for n in projects.notes] == ["из другого окна"])


def test_mentions_in_the_composer(app):
    mentions, client = app.mentions, app.client
    wait_until(lambda: app.projects.items)
    app.newChat()
    mentions.typed("смотри @Lo", 10)
    assert mentions.start == 7 and [i["name"] for i in mentions.items] == ["LocalChat"]
    mentions.typed("смотри @LocalChat/ap", 20)  # a project's files, from the hub
    wait_until(lambda: mentions.items and mentions.items[0]["kind"] == "file")
    assert mentions.items[0]["insert"] == "@LocalChat/src/app.py "
    mentions.typed("смотри @LocalChat/src/app.py готово", 35)  # the cursor is past the mention
    assert mentions.items == [] and mentions.start == -1

    # a project named in passing is offered; waved away, it stays away in this chat
    mentions.typed("почему localchat упал?", 5)
    assert mentions.suggestions == [{"id": "p1", "name": "LocalChat"}]
    mentions.waveAway()
    assert mentions.suggestions == []
    app.newChat()  # another chat: offered again
    assert mentions.suggestions == [{"id": "p1", "name": "LocalChat"}]
    mentions.attachSuggested()
    assert [p["id"] for p in app.chatProjects] == ["p1"] and mentions.suggestions == []
    assert client.log == []  # a new chat: the project goes with its first message

    # @project attaches, @project/file pins the file - both before the message goes out
    app.newChat()
    app.send("глянь @LocalChat/src/app.py, что там?")
    wait_until(lambda: client.sent)
    assert client.log == [
        ("projects", "c2", ["p1"]),
        ("context", "c2", {"pinned": [{"project_id": "p1", "path": "src/app.py"}]}),
        ("send", "c2", "глянь @LocalChat/src/app.py, что там?"),
    ]
    assert "В контексте чата: проект LocalChat, файл src/app.py" in app.flashText


def test_links_between_projects(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.currentId == "p1")
    assert projects.links["confirmed"] == [] and projects.links["proposed"] == []
    projects.scanLinks()
    wait_until(lambda: projects.links["proposed"])
    proposal = projects.links["proposed"][0]
    assert proposal["title"] == "LocalChat → llama-swap: использует" and "8080" in proposal["evidence"][0]
    assert "Найдено новых связей: 1" in app.flashText
    projects.confirmLink(7, "OpenAI-совместимый API на :8080")
    wait_until(lambda: projects.links["confirmed"])
    link = projects.links["confirmed"][0]
    assert (link["arrow"], link["other"], link["kind"]) == ("→", "llama-swap", "использует")
    assert client.log[-1] == ("link", 7, {"status": "confirmed", "description": "OpenAI-совместимый API на :8080"})
    projects.deleteLink(7)
    wait_until(lambda: projects.links["confirmed"] == [])

    from app.ui import feed_rows

    payload = {"a_name": "LocalChat", "b_name": "llama-swap", "kind": "uses", "description": ":8080"}
    shown = feed_rows.describe({"type": "link.confirmed", "payload": payload}, projects={}, chats={})
    assert shown["title"] == "Связь: LocalChat → llama-swap, использует (:8080)"


def test_key_sequences():
    from app.ui.terminals import ALT, CTRL, SHIFT, key_sequence

    k = Qt.Key
    assert key_sequence(k.Key_Up.value, 0, "") == "\x1b[A"
    assert key_sequence(k.Key_Up.value, 0, "", app_cursor=True) == "\x1bOA"  # what vim and less ask for
    assert key_sequence(k.Key_Left.value, CTRL, "") == "\x1b[1;5D"
    assert key_sequence(k.Key_Delete.value, 0, "") == "\x1b[3~"
    assert key_sequence(k.Key_F1.value, 0, "") == "\x1bOP"
    assert key_sequence(k.Key_F5.value, 0, "") == "\x1b[15~"
    assert key_sequence(k.Key_Return.value, 0, "\r") == "\r"
    assert key_sequence(k.Key_Backspace.value, 0, "\x08") == "\x7f"
    assert key_sequence(k.Key_C.value, CTRL, "\x03") == "\x03"
    assert key_sequence(k.Key_Tab.value, SHIFT, "") == "\x1b[Z"
    assert key_sequence(k.Key_X.value, ALT, "x") == "\x1bx"
    assert key_sequence(0x0436, 0, "ж") == "ж"
    assert key_sequence(k.Key_Shift.value, SHIFT, "") == ""


def test_terminal_panel(app):
    from app.ui.terminals import CTRL

    terms, client = app.terminals, app.client
    app.projects.openProject("p1")
    wait_until(lambda: terms.tabs)
    assert [(t["title"], t["running"], t["note"]) for t in terms.tabs] == [("PowerShell", True, "работает")]
    terms.setViewSize(40, 5)
    terms.openTerminal("t1")
    wait_until(lambda: terms._emus["t1"].loaded)
    assert terms.lines.rows[0]["html"] == "PS&nbsp;D:\\Code&gt;"
    assert terms.cursorVisible and (terms.cursorX, terms.cursorY) == (len(TAIL), 0)

    # an event that overlaps the tail: only its new part is played
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(TAIL) - 3, "data": "e> dir\r\n"})
    assert terms.lines.rows[0]["html"] == "PS&nbsp;D:\\Code&gt;&nbsp;dir"
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(TAIL) + 5,
                                  "data": "".join(f"\x1b[32mline {n}\x1b[0m\r\n" for n in range(8))})  # fmt: skip
    keys = [r["key"] for r in terms.lines.rows]
    assert len(keys) == 5 + 5 and keys[:5] == [f"h{n}" for n in range(5)]  # 5 lines scrolled into history
    assert any("color:#0dbc79" in r["html"] for r in terms.lines.rows)  # green, from the dark palette

    # keys go out in order; Ctrl+C is the interrupt
    assert terms.key(Qt.Key.Key_A.value, 0, "a")
    assert terms.key(Qt.Key.Key_Return.value, 0, "\r")
    assert terms.key(Qt.Key.Key_C.value, CTRL, "\x03")
    wait_until(lambda: len(client.inputs) == 3)
    assert [d for _, d in client.inputs] == ["a", "\r", "\x03"]
    terms.setViewSize(50, 6)
    wait_until(lambda: client.resized == [("t1", 50, 6)])

    # it ends: no more typing, the tab says how
    send(app, "terminal.exited", {"terminal_id": "t1", "exit_code": 0})
    assert terms.tabs[0]["note"] == "завершён, код 0" and not terms.currentRunning and not terms.cursorVisible
    assert not terms.key(Qt.Key.Key_A.value, 0, "a")

    # a new one; another project's terminal is not shown
    terms.newTerminal()
    wait_until(lambda: terms.currentId == "t2")
    assert terms.panelOpen and [t["id"] for t in terms.tabs] == ["t2", "t1"]  # running first
    send(app, "terminal.started", {"terminal": {**TERMINAL, "id": "x9", "project_id": "p9"}})
    assert len(terms.tabs) == 2
    terms.closeTab("t2")  # running: asks, then stops
    assert "PowerShell" in app.question["title"]
    app.answer(True)
    wait_until(lambda: client.term_stopped == ["t2"])
    terms.closeTab("t1")  # ended: removed with its log
    wait_until(lambda: [t["id"] for t in terms.tabs] == ["t2"])
    assert client.term_removed == ["t1"]


def test_projects_mode_and_palette(app):
    asked = []
    app.uiRequested.connect(asked.append)
    assert app.mode == "chats"
    app.setMode("projects")
    assert app.mode == "projects" and app.settings.value("ui/mode") == "projects"
    items = app.paletteItems("")
    assert {"Проекты"} <= {i["section"] for i in items if i.get("section")}
    assert any(i["id"] == "project:p1" for i in items)
    app.setMode("chats")
    app.runCommand("project:p1")
    assert app.mode == "projects"
    wait_until(lambda: app.projects.currentId == "p1")
    app.runCommand("action:add-project")
    assert asked == ["add-project"]
    app.runCommand("action:new")
    assert app.mode == "chats"
    app.runCommand("action:mode:projects")
    assert app.mode == "projects"
    app.setMode("nonsense")
    assert app.mode == "projects"


def test_the_projects_view_without_warnings(qapp):
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("projects")
        for _ in range(10):
            QApplication.processEvents()
        app.projects.openProject("p1")
        wait_until(lambda: len(app.projects.tree.rows) == 3 and app.projects.git.get("repo"))
        app.projects.toggleDir(0)
        wait_until(lambda: len(app.projects.tree.rows) == 5)
        for _ in range(10):
            QApplication.processEvents()
        tree = quick.window.findChild(QObject, "projectTree")
        assert tree.property("count") == 5
        overview = quick.window.findChild(QObject, "projectOverview")
        assert overview.property("visible") and overview.property("contentHeight") > 200
        wait_until(lambda: len(app.projects.processes) == 3)
        for _ in range(10):
            QApplication.processEvents()
        processes = quick.window.findChild(QObject, "projectProcesses")
        assert processes.property("visible") and processes.property("height") > 3 * 30
        # a question answered before its dialog finished opening still closes the dialog
        dialog = quick.window.findChild(QObject, "confirmDialog")
        app.projects.askStopProcess(10)
        QApplication.processEvents()
        app.answer(False)
        wait_until(lambda: not dialog.property("visible"))
        app.projects.openFile("src/app.py", False)
        wait_until(lambda: app.projects.file.get("path") == "src/app.py")
        for _ in range(10):
            QApplication.processEvents()
        code = quick.window.findChild(QObject, "codeView")
        assert code.property("visible") and code.property("contentHeight") > 40
        app.projects.showDiff(True)
        wait_until(lambda: app.projects.file.get("diffMode"))
        app.setTheme("light")
        app.projects.openFile("logo.png", False)
        wait_until(lambda: app.projects.file.get("path") == "logo.png")
        for _ in range(20):
            QApplication.processEvents()
        assert not code.property("visible")
        # the terminal panel under the project
        app.terminals.setPanelOpen(True)
        wait_until(lambda: app.terminals.tabs)
        app.terminals.openTerminal("t1")
        wait_until(lambda: app.terminals.lines.rows)
        for _ in range(20):
            QApplication.processEvents()
        panel = quick.window.findChild(QObject, "terminalPanel")
        assert panel.property("visible") and panel.property("height") > 100
        assert quick.window.findChild(QObject, "terminalLines").property("count") == len(app.terminals.lines.rows)
        # search: results in the sidebar, a file opened at a line with the line marked
        app.projects.search("print", False, False)
        wait_until(lambda: app.projects.searchRows)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "searchResults").property("count") == 3
        app.projects.openFileAt("src/app.py", 2)
        wait_until(lambda: app.projects.file.get("gotoLine") == 2)
        for _ in range(20):
            QApplication.processEvents()
        mark = quick.window.findChild(QObject, "gotoMark")
        assert mark.property("visible") and mark.property("y") > 10  # the second line, below the first
        # definitions: symbol rows in the sidebar, the outline of the open file
        app.projects.setSymbolSearch(True)
        app.projects.search("run", False, False)
        wait_until(lambda: app.projects.searchRows and app.projects.searchRows[0]["kind"] == "symbol")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "searchResults").property("count") == 1
        app.projects.setSymbolSearch(False)
        assert quick.window.findChild(QObject, "outlineButton").property("visible")
        from PySide6.QtCore import QMetaObject

        outline = quick.window.findChild(QObject, "outlinePopup")
        app.projects.loadOutline()
        QMetaObject.invokeMethod(outline, "open")
        wait_until(lambda: outline.property("opened") and app.projects.outline["state"] == "ready")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "outlineList").property("count") == 2
        QMetaObject.invokeMethod(outline, "close")
        # the feed
        from PySide6.QtCore import Q_ARG, QMetaObject

        sheet = quick.window.findChild(QObject, "feedSheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened") and app.feed.items)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "feedItems").property("height") > 4 * 30
        QMetaObject.invokeMethod(sheet, "close")
        for _ in range(20):
            QApplication.processEvents()
        project_sheet = quick.window.findChild(QObject, "projectSheet")
        QMetaObject.invokeMethod(project_sheet, "open")
        wait_until(lambda: project_sheet.property("opened"))
        assert quick.window.findChild(QObject, "projectCloud").property("value") == "local"
        # a chat whose answer used tools: the lines render, one opens
        app.setMode("chats")
        app.openChat("ct")
        wait_until(lambda: len(app.messages.rows) == 3)
        app.toggleTool(1, 0)
        for _ in range(20):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "chatProjects").property("visible")
        assert quick.window.findChild(QObject, "contextChip").property("visible")
        # a project named in the draft: the offer to attach it shows above the composer
        app.toggleChatProject("p1")
        app.mentions.typed("почему LocalChat упал?", 3)
        for _ in range(10):
            QApplication.processEvents()
        bar = quick.window.findChild(QObject, "mentionSuggestion")
        assert bar.property("visible") and bar.property("height") > 20
        app.mentions.attachSuggested()
        for _ in range(10):
            QApplication.processEvents()
        assert not bar.property("visible")
        context_sheet = quick.window.findChild(QObject, "contextSheet")
        QMetaObject.invokeMethod(context_sheet, "open")
        wait_until(lambda: context_sheet.property("opened") and app.contextBlocks)
        app.toggleContextBlock(0)
        for _ in range(20):
            QApplication.processEvents()
        QMetaObject.invokeMethod(context_sheet, "close")
        for _ in range(20):
            QApplication.processEvents()
        QMetaObject.invokeMethod(project_sheet, "close")
        for _ in range(20):
            QApplication.processEvents()
        app.setMode("projects")
        app.projects.closeFile()  # the overview shows when no file is open
        for _ in range(20):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "projectCard").property("visible")
        assert quick.window.findChild(QObject, "projectNotes").property("visible")
        app.projects.scanLinks()
        wait_until(lambda: app.projects.links["proposed"])
        for _ in range(20):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "projectLinks").property("height") > 100
        # a confirmed card the project has moved on from: the hint and the update button
        confirmed = {"version": 1, "text": "## Назначение\nДемо", "model_id": None,
                     "created_at": "2026-10-27T03:00:00Z"}  # fmt: skip
        changes = {"commits": 30, "new_folders": [], "gone_folders": [], "dependencies": [], "significant": True}
        app.client.card_state = {**app.client.card_state, "confirmed": confirmed, "versions": [confirmed],
                                 "changes": changes}  # fmt: skip
        app.projects.load_card()
        wait_until(lambda: app.projects.card["changes"])
        for _ in range(20):
            QApplication.processEvents()
        stale = quick.window.findChild(QObject, "cardStale")
        assert stale.property("visible") and stale.property("height") > 20
        card_sheet = quick.window.findChild(QObject, "cardSheet")
        QMetaObject.invokeMethod(
            card_sheet, "openWith", Q_ARG("QVariant", "## Назначение\nДемо"), Q_ARG("QVariant", True)
        )
        wait_until(lambda: card_sheet.property("opened"))
        assert quick.window.findChild(QObject, "cardEditor").property("text") == "## Назначение\nДемо"
        QMetaObject.invokeMethod(card_sheet, "close")
        for _ in range(20):
            QApplication.processEvents()
        image = quick.window.grabWindow()
        assert not image.isNull()
        assert quick.warnings == []
    finally:
        quick.controller.setMode("chats")
        quick.close()
        quick.theme.setMode("dark")


def test_search_lines_start_near_their_match():
    rows_ = project_rows.search_rows(
        {
            "files": [
                {
                    "path": "a.py",
                    "matches": [{"line": 3, "text": "    from .terminals import Manager", "ranges": [[27, 34]]}],
                }
            ]
        }
    )
    html = rows_[1]["html"]
    assert html.startswith("…") and ">Manager</span>" in html and "from" not in html
