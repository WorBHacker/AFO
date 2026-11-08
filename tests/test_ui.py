"""The Qt Quick window: its controller against a fake hub client, its row builders, and the real QML loaded with
history and a streamed answer (no QML warnings allowed). Offscreen, software scene graph."""

import base64
import copy
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import QGuiApplication as QApplication  # a QML window needs no widgets
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from app import palette
from app.hub_client import HubError
from app.ui import commands, editor, project_rows, rows
from app.ui.controller import Controller
from app.ui.models import TREE_ROLES, RowModel
from app.ui.theme import Theme, qml_colour
from common.paths import data_paths
from tests.test_visual import PICTURE

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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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


STATS = {"model_id": "llama-swap/coder", "requests": 56, "errors": 14, "error_rate": 0.2, "tok_s": 102.1,
         "ttft_s": 0.49, "answer_s": 1.65, "cost_usd": 0.0, "estimate_usd": 0.0, "runs": 3, "runs_done": 2}  # fmt: skip

AGENT = {
    "id": "a1",
    "name": "Ревьюер",
    "version": 3,
    "instructions": "Читай код и говори, что не так.",
    "model_id": "llama-swap/coder",
    "tools": ["read_file"],
    "mode": "read_only",
    "project_ids": ["p1"],
    "max_steps": 20,
    "max_minutes": 15,
    "budget_usd": None,
}
RUN = {
    "id": "r1",
    "conversation_id": "r1",
    "profile_name": "Ревьюер",
    "task": "Проверь   hub/chat.py",
    "status": "running",
    "shown_status": "waiting",
    "detail": None,
    "steps": 3,
    "cost_usd": 0,
    "created_at": "2026-10-27T06:00:00.000Z",
}

CHANGE = {
    "id": 3,
    "created_at": "2026-10-27T04:00:00.000Z",
    "project_id": "p1",
    "path": "src/app.py",
    "before_hash": "aa",
    "after_hash": "bb",
    "tool": "edit_file",
    "conversation_id": "c1",
    "approval_id": "ap0",
    "reverted_at": None,
    "chat": "Первый",
}

PROGRAMS = [
    {"id": "p7z", "name": "7-Zip", "publisher": "Igor Pavlov", "version": "26.02", "kind": "desktop",
     "exe": "C:\\Program Files\\7-Zip\\7zFM.exe", "folder": "C:\\Program Files\\7-Zip", "aumid": "",
     "decision": "allow", "decided_at": "2026-10-27T15:00:00Z", "first_seen": "2026-10-27T14:00:00Z",
     "removed": False, "fresh": False},
    {"id": "pnp", "name": "Блокнот", "publisher": "Microsoft Corporation", "version": "11.2607", "kind": "store",
     "exe": "", "folder": "C:\\Program Files\\WindowsApps\\Notepad", "aumid": "Notepad!App", "decision": None,
     "decided_at": None, "first_seen": "2026-10-27T14:00:00Z", "removed": False, "fresh": False},
    {"id": "ptg", "name": "Telegram", "publisher": "", "version": "", "kind": "desktop", "exe": "C:\\T\\Telegram.exe",
     "folder": "C:\\T", "aumid": "", "decision": None, "decided_at": None, "first_seen": "2026-10-27T16:00:00Z",
     "removed": False, "fresh": True},
    {"id": "pgo", "name": "Старая", "publisher": "", "version": "", "kind": "desktop", "exe": "C:\\Old\\old.exe",
     "folder": "C:\\Old", "aumid": "", "decision": None, "decided_at": None, "first_seen": "2026-10-20T10:00:00Z",
     "removed": True, "fresh": False},  # gone and never decided: not shown
]  # fmt: skip
APPROVAL = {
    "id": "ap1",
    "created_at": "2026-10-27T01:02:03.000Z",
    "conversation_id": "c1",
    "project_id": "p1",
    "client": "hub-claude-code",
    "tool": "Edit",
    "kind": "write",
    "destructive": False,
    "status": "pending",
    "input": {"file_path": "D:\\Code\\LocalChat\\hub\\chat.py", "old_string": "a", "new_string": "b"},
    "preview": {
        "kind": "diff",
        "note": "D:\\Code\\LocalChat\\hub\\chat.py",
        "text": "--- a/hub/chat.py\n+++ b/hub/chat.py\n@@ -1,2 +1,2 @@\n x = 1\n-a\n+b\n",
    },
}


PARTED_HUNKS = [{"index": i, "header": f"@@ -{n},4 +{n},4 @@", "text": f" # {n}\n-x = 1\n+x = 2\n # {n + 2}\n"}
                for i, n in enumerate((1, 17, 37))]  # fmt: skip
PARTED = {
    "kind": "diff",
    "note": "conf.py",
    "hunks": PARTED_HUNKS,
    "text": "--- a/conf.py\n+++ b/conf.py\n" + "".join(f"{h['header']}\n{h['text']}" for h in PARTED_HUNKS),
}


LLAMA = [
    {"id": "coder", "name": "Coder", "engine": "strata", "editable": False, "ttl": 0,
     "profile": "C:/Strata/launch/coder.json"},
    {"id": "big", "name": "Big", "engine": "llama.cpp", "editable": True, "ttl": 0, "file": "F:/big.gguf",
     "mmproj": "", "mmproj_on_gpu": True, "context": 32768, "gpu_layers": "all", "flash_attn": "on",
     "cache_type": "q8_0", "parallel": 1,
     "info": {"layers": 64, "kv_layers": 16, "kv_heads": 4, "key_length": 256, "value_length": 256,
              "size_bytes": 12 * 1024**3}},
]  # fmt: skip
AUTO_RUN = {**RUN, "id": "auto1", "conversation_id": "auto1", "kind": "autonomous", "goal": "Посчитать строки",
            "status": "running", "shown_status": "running", "model_id": "llama-swap/coder", "report": None,
            "steps": 4, "cost_usd": 0}  # fmt: skip
AUTO_PLAN = {"goal": "Посчитать строки", "done_when": "число названо", "autonomy": "plan", "report": None,
             "paused": False, "plans": [{"version": 2, "reason": "сбивался", "approved": "user", "steps": [
                 {"n": 1, "title": "Прочитать", "check": "файл прочитан", "status": "done", "attempts": 0,
                  "note": "прочитан · проверка: 9 строк",
                  "files": [{"path": "README.md", "status": "changed", "added": 1, "removed": 0}],
                  "mismatch": "условие «не трогать: README.md» — изменён README.md"},
                 {"n": 2, "title": "Посчитать", "check": "дважды", "status": "running", "attempts": 1, "note": ""}]}],
             "questions": [{"id": "q1", "question": "Считать пустые?", "status": "pending"}]}  # fmt: skip
PRESETS = {
    "precise": {"temperature": 0.2, "top_p": 0.9, "top_k": 20},
    "creative": {"temperature": 1.0, "top_p": 0.98, "top_k": 100},
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
        self.password = None  # the connections' (D13)
        self.started = {"login": False, "llama_swap": True, "llama_swap_found": True, "llama_swap_script": "x.cmd"}
        self.languages = []  # what the window told the hub (10.5)
        self.release_state = {
            "current": "0.1.0",
            "latest": "",
            "newer": False,
            "size": 0,
            "url": "",
            "notes": "",
            "published": "",
            "auto": True,
            "installed": True,
            "checked": "",
            "error": "",
            "job": {"status": "idle"},
        }  # AFO's own updates (10.4d)
        self.release_calls = []
        self.ticket = ""
        self.chosen = []
        self.project_list = [dict(PROJECT)]
        self.trees = copy.deepcopy(TREE)
        self.files = dict(FILES)
        self.tree_calls = []
        self.stopped = []
        self.term_list = [dict(TERMINAL)]
        self.term_text = TAIL  # all a terminal printed; the hub's memory keeps the last `term_kept` characters of it
        self.term_kept = 1_000_000
        self.output_asked = []  # `since` of each read of a terminal's output
        self.inputs = []
        self.resized = []
        self.term_stopped = []
        self.term_removed = []
        self.file_calls = []
        self.saves = []  # the editor's saves: (pid, path, text, encoding, base)
        self.unsaved = []  # what the editor told the hub it holds unsaved, each time (14.2c)
        self.updates = []
        self.searches = []
        self.symbol_calls = []
        self.log = []
        self.histories = {"ct": TOOL_HISTORY}
        self.note_list = []
        self.link_list = []
        self.card_state = {"confirmed": None, "draft": None, "versions": [], "missing": [], "drafting": False}
        self.pending = [dict(APPROVAL)]
        self.change_list = [dict(CHANGE)]
        self.rule_list = [{"id": 7, "project_id": "p1", "grant_key": "edit_file", "created_by": "app"}]
        self.stale = True  # the changed file was edited since: an undo needs force
        self.agent_list = [dict(AGENT)]
        self.advice_calls = []
        self.run_list = [dict(RUN)]
        self.program_list = [dict(p) for p in PROGRAMS]
        self.program_offer = None
        self.program_calls = []
        self.answers = []
        self.claude_terminals = []
        self.model_saved = {}  # model id -> [settings of each version]
        self.exported = {}
        self.auto_calls = []
        self.scheduled = []
        self.mailbox = None
        self.mail_calls = []
        self.keys = []
        self.key_calls = []
        self.sandboxed = []
        self.task_sandboxes = []  # a coordinator's tasks' sandboxes (16.2)
        self.boards = {}  # a coordinator's run -> its tasks (16.4)
        self.sandbox_calls = []
        self.schedule_calls = []
        self.install_calls = []
        self.install_jobs_list = []
        self.visual_calls = []  # «Нарисовать» (29.5)
        self.visual_jobs_list = []
        self.visual_rungs = {}  # the rung each model came out on here (29.4б)
        self.visual_videos = []  # the video models (29.8)
        self.found = {"job": {"status": "idle"}, "at": "", "models": []}  # the models on the drives (15.1)
        self.tune_status = {"job": {"status": "idle", "queue": []}, "results": {}, "auto": True}  # 15.2
        self.tune_calls = []
        self.setup = {
            "needed": False,
            "answering": True,
            "engine": {},
            "cards": [],
            "builds": {},
            "job": {"status": "idle"},
        }
        self.quiet = {"enabled": True, "from": "23:00", "to": "08:00"}
        self.memory_calls = []
        self.access = {
            "folders": [{"path": "D:/Reports", "write": False}],
            "domains": ["pypi.org"],
            "default_domains": ["docs.python.org", "pypi.org"],
        }
        self.access_calls = []
        self.mcp = [
            {
                "name": "broken",
                "transport": "stdio",
                "target": "nope --stdio",
                "enabled": True,
                "state": "error",
                "detail": "FileNotFoundError: nope",
                "env": [],
                "tools": [],
            }
        ]
        self.mcp_calls = []
        self.fact_list = [
            {
                "id": 1,
                "text": "Отчёты — в Markdown",
                "about": "отчёты",
                "source": "run:auto1",
                "personal": False,
                "cloud_ok": True,
                "created_at": "2026-10-27T20:00:00Z",
            },
            {
                "id": 2,
                "text": "Почта ivan@mail.ru",
                "about": "",
                "source": "user",
                "personal": True,
                "cloud_ok": False,
                "created_at": "2026-10-27T21:00:00Z",
            },
        ]
        self.skill_list = [
            {
                "id": 1,
                "name": "Подсчёт строк",
                "goal": "Посчитать строки",
                "uses": 2,
                "steps": [{"title": "Прочитать", "check": ""}, {"title": "Посчитать", "check": ""}],
                "created_at": "2026-10-27T22:00:00Z",
            }
        ]
        self.imported = []
        self.model_calls = []
        self.settings_calls = []

    def models(self):
        return MODELS

    # --- the models' settings (stage 8) ---

    def _model_info(self, model_id):
        versions = self.model_saved.get(model_id, [])
        accepts = [] if not model_id else ["max_tokens", "stop"] if model_id.startswith("anthropic/") else [
            "max_tokens", "min_p", "repeat_penalty", "stop", "temperature", "thinking", "top_k", "top_p"]  # fmt: skip
        return {"model_id": model_id, "model": next((m for m in MODELS if m["id"] == model_id), None),
                "version": len(versions), "settings": versions[-1] if versions else {},
                "history": [{"version": n + 1, "settings": v, "created_at": "2026-10-27T18:00:00.000Z", "by": "app"}
                            for n, v in reversed(list(enumerate(versions)))],
                "accepts": accepts, "presets": PRESETS}  # fmt: skip

    def model_settings(self, model_id):
        self.model_calls.append(("read", model_id))
        return self._model_info(model_id)

    def save_model_settings(self, model_id, settings):
        self.model_calls.append(("save", model_id, settings))
        if settings.get("params", {}).get("temperature", 0) > 2:
            raise HubError("температура: от 0 до 2")
        self.model_saved.setdefault(model_id, []).append(settings)
        return self._model_info(model_id)

    def revert_model_settings(self, model_id, version):
        self.model_calls.append(("revert", model_id, version))
        self.model_saved[model_id].append(self.model_saved[model_id][version - 1])
        return self._model_info(model_id)

    def tune_state(self):
        return json.loads(json.dumps(self.tune_status))

    def tune(self, model_id):
        self.tune_calls.append(("tune", model_id))
        self.tune_status["job"] = {"status": "measuring", "model_id": model_id, "done": 0, "total": 12, "queue": []}
        return self.tune_state()

    def tune_all(self):
        self.tune_calls.append(("all",))
        return self.tune_state()

    def tune_cancel(self):
        self.tune_calls.append(("cancel",))
        self.tune_status["job"] = {"status": "idle", "queue": []}
        return self.tune_state()

    def set_tune_auto(self, on):
        self.tune_calls.append(("auto", on))
        self.tune_status["auto"] = on
        return self.tune_state()

    def llama_models(self):
        return {"models": [dict(m) for m in LLAMA], "vram_total_mib": 16384, "backups": []}

    def change_llama_model(self, model_id, changes):
        self.model_calls.append(("launch", model_id, changes))
        LLAMA_DONE = [{**m, **changes} if m["id"] == model_id else m for m in LLAMA]
        return {"changed": True, "verified": True, "backup": "config.yaml.bak-2026-10-27-230000",
                "models": LLAMA_DONE}  # fmt: skip

    def trial_llama_model(self, model_id):
        self.model_calls.append(("trial", model_id))
        return {"ok": False, "error": "llama-swap: upstream command exited", "restored": "config.yaml.bak-1"}

    def memory_facts(self, query=""):
        return [f for f in self.fact_list if query.lower() in f["text"].lower()]

    def add_fact(self, text, about="", personal=False):
        self.memory_calls.append(("add", text, personal))
        return {"id": 9}

    def change_fact(self, fact_id, **changes):
        self.memory_calls.append(("change", fact_id, changes))
        return {"id": fact_id}

    def delete_fact(self, fact_id):
        self.memory_calls.append(("delete", fact_id))

    def skills(self, query=""):
        return self.skill_list

    def add_skill(self, run_id, name=""):
        self.memory_calls.append(("skill", run_id, name))
        return {"id": 1, "name": name}

    def delete_skill(self, skill_id):
        self.memory_calls.append(("delete skill", skill_id))

    def agent_access(self):
        return dict(self.access)

    def set_agent_access(self, **changes):
        self.access_calls.append(changes)
        self.access = {**self.access, **changes}
        return dict(self.access)

    def support_check(self):
        self.install_calls.append(("check",))
        return {
            "lines": [
                {"id": "hub", "title": "Хаб", "ok": True, "text": "работает", "advice": ""},
                {
                    "id": "disk-F",
                    "title": "Диск F:",
                    "ok": None,
                    "text": "жёсткий диск",
                    "advice": "Модели на жёстком диске загружаются в разы медленнее.",
                },
            ]
        }

    def support_report(self):
        self.install_calls.append(("report",))
        return {"path": "C:/data/reports/afo-report.zip", "folder": "", **self.support_check()}

    def setup_state(self):
        return dict(self.setup)

    def setup_models(self):
        best = {"quant": "Q8_0", "size": 8_030_000_000, "verdict": "gpu", "words": "помещается в видеопамять — быстро"}
        return {
            "models": {
                "chat": {
                    "name": "Gemma 4 E4B",
                    "repo": "ggml-org/gemma-4-E4B-it-GGUF",
                    "recommended": best,
                    "installed": [],
                },
                "code": {
                    "name": "Qwen2.5 Coder 7B",
                    "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
                    "recommended": {**best, "size": 8_100_000_000},
                    "installed": ["x"],
                },
                "vision": None,
                "image": {
                    "name": "FLUX.2 [klein] 4B",
                    "repo": "pictures:flux2-klein-4b",
                    "recommended": {"quant": "", "size": 4_930_000_000, "verdict": "gpu", "words": "быстро"},
                    "installed": [],
                },
            }
        }

    def install_pictures(self):
        return {
            "models": [
                {
                    "id": "sdxs-512",
                    "name": "SDXS 512",
                    "purposes": ["image"],
                    "purpose_words": ["Рисовать"],
                    "tier": "light",
                    "license": "openrail++",
                    "about": {"ru": "Самая лёгкая"},
                    "size": 690_000_000,
                    "verdict": "fast",
                    "words": "быстро",
                    "recommended": False,
                    "installed": ["sdxs-512"],
                },
                {
                    "id": "z-image-turbo",
                    "name": "Z-Image-Turbo",
                    "purposes": ["image"],
                    "purpose_words": ["Рисовать"],
                    "tier": "quality",
                    "license": "apache-2.0",
                    "about": {"ru": "Качественнее"},
                    "size": 6_250_000_000,
                    "verdict": "slow",
                    "words": "медленно — минуты на картинку",
                    "recommended": False,
                    "installed": [],
                },
            ],
            "machine": {"build": "cuda12", "pictures": "main"},
        }

    def visual_models(self):
        models = [{"model_id": "flux2-klein-4b", "name": "FLUX.2 [klein] 4B", "tier": "main", "size": 1024,
                   "steps": 4, "cfg": 1.0},
                  {"model_id": "z-image-turbo", "name": "Z-Image-Turbo", "tier": "quality", "size": 1024,
                   "steps": 8, "cfg": 1.0}]  # fmt: skip
        return {"models": [{"pixels": 0, "instead": "", **m, **self.visual_rungs.get(m["model_id"], {})}
                           for m in models], "default": "flux2-klein-4b", "videos": self.visual_videos}  # fmt: skip

    def visual_video(self, spec):
        self.visual_calls.append(("video", spec))
        job = {"id": f"v{len(self.visual_calls)}", "kind": "video", "prompt": spec["prompt"], "model_id": spec["model"],
               "status": "queued", "width": 832, "height": 480, "frames": 49, "fps": 24, "seed": 7, "error": "",
               "seconds": None, "file": ""}  # fmt: skip
        self.visual_jobs_list.insert(0, job)
        return {"job": job}

    def visual_draw(self, spec):
        self.visual_calls.append(("draw", spec))
        job = {"id": f"v{len(self.visual_calls)}", "prompt": spec["prompt"], "model_id": spec["model"],
               "status": "queued", "width": spec["width"], "height": spec["height"], "seed": 7, "error": "",
               "seconds": None, "file": ""}  # fmt: skip
        self.visual_jobs_list.insert(0, job)
        return {"job": job}

    def visual_jobs(self, limit=30):
        return {"jobs": list(self.visual_jobs_list)}

    def visual_cancel(self, job_id):
        self.visual_calls.append(("cancel", job_id))
        return {"job": {"id": job_id, "status": "running"}}

    def visual_file(self, job_id):
        return PICTURE

    def visual_mp4(self, job_id):
        self.visual_calls.append(("mp4", job_id))
        return b"MP4 of " + job_id.encode()

    def visual_picture(self, blob):
        self.visual_calls.append(("picture", blob))
        return PICTURE

    def install_pictures_start(self, spec):
        self.install_calls.append(("pictures", spec))
        job = {"id": "p1", "repo": "leejet/Z-Image-Turbo-GGUF", "quant": "Q4_K", "name": "Z-Image-Turbo",
               "model_id": "z-image-turbo", "status": "queued", "done_bytes": 0, "total_bytes": 6_250_000_000,
               "percent": 0.0, "error": "", "trial": {}, "files": [], "plan": {"set": spec["id"]}}  # fmt: skip
        self.install_jobs_list = [job]
        return {"job": job}

    def setup_engine(self, build=""):
        self.install_calls.append(("engine", build))
        self.setup["job"] = {"status": "downloading", "build": build, "done": 0, "total": 0, "step": ""}
        return {"job": dict(self.setup["job"])}

    def setup_update_info(self):
        return {"current": "b11899", "build": "cuda-13", "latest": "b11946", "newer": True, "server": "x"}

    def setup_update(self):
        self.install_calls.append(("update",))
        return {"job": {"status": "downloading", "update": True, "done": 0, "total": 0}}

    def setup_cancel(self):
        self.install_calls.append(("engine-cancel",))
        return {"job": {"status": "cancelled"}}

    def install_system(self):
        return {
            "space": {"gpu": "RTX 5080", "vram_total_gib": 15.9, "vram_gib": 14.2, "ram_gib": 42.0, "loaded": []},
            "drives": [
                {"letter": "D", "kind": "nvme", "words": "NVMe", "free_gib": 456.5},
                {"letter": "F", "kind": "hdd", "words": "жёсткий диск (медленно)", "free_gib": 233.0},
            ],
            "fits_params": 22_400_000_000,
            "server": "${llama_server}",
            "problem": "",
            "folder": "D:\\AI model\\LocalChat\\model",
            "home": {
                "folder": "D:\\AI model\\LocalChat\\model",
                "letter": "D",
                "words": "NVMe",
                "kind": "nvme",
                "free_gib": 456.5,
            },
            "token": False,
        }

    def install_catalog(self):
        best = {"quant": "Q8_0", "size": 8_030_000_000, "verdict": "gpu", "words": "помещается в видеопамять — быстро"}
        return {
            "models": [
                {
                    "id": "gemma-4-e4b",
                    "repo": "ggml-org/gemma-4-E4B-it-GGUF",
                    "name": "Gemma 4 E4B",
                    "purposes": ["chat", "vision"],
                    "purpose_words": ["Разговоры", "Картинки"],
                    "about": {"ru": "Небольшая"},
                    "license": "apache-2.0",
                    "gated": False,
                    "recommended": best,
                    "installed": [],
                    "moe": False,
                },
                {
                    "id": "gpt-oss-120b",
                    "repo": "ggml-org/gpt-oss-120b-GGUF",
                    "name": "gpt-oss 120B",
                    "purposes": ["chat", "code"],
                    "purpose_words": ["Разговоры", "Код"],
                    "about": {"ru": "Очень большая"},
                    "license": "apache-2.0",
                    "recommended": None,
                    "smallest": {"quant": "MXFP4", "size": 63_390_000_000},
                    "installed": [],
                },
            ]
        }

    def install_search(self, query, *, task="", size="", sort="downloads", plain=False):
        self.install_calls.append(("search", {"q": query, "task": task, "size": size, "sort": sort, "plain": plain}))
        if query == "nothing":
            return {"models": []}
        return {
            "models": [
                {
                    "repo": "someone/Tiny-abliterated-GGUF",
                    "downloads": 1_234_567,
                    "likes": 12,
                    "params": 27_320_000_000,
                    "license": "mit",
                    "unrestricted": True,
                    "gated": False,
                    "updated": "2026-10-01",
                }
            ]
        }

    def install_repo(self, repo):
        return {
            "repo": repo,
            "license": "apache-2.0",
            "params": 7_600_000_000,
            "context_trained": 131072,
            "unrestricted": False,
            "gated": False,
            "page": "https://huggingface.co/" + repo,
            "projector": {"file": "mmproj-Q8_0.gguf", "size": 560_000_000},
            "variants": [
                {
                    "quant": "Q4_0",
                    "size": 4_590_000_000,
                    "bits": 4.9,
                    "verdict": "gpu",
                    "words": "помещается в видеопамять — быстро",
                    "recommended": False,
                    "files": [{}],
                },
                {
                    "quant": "Q8_0",
                    "size": 8_030_000_000,
                    "bits": 8.5,
                    "verdict": "gpu",
                    "words": "помещается в видеопамять — быстро",
                    "recommended": True,
                    "files": [{}],
                },
            ],
        }

    def install(self, spec):
        self.install_calls.append(("install", spec))
        job = {
            "id": "j1",
            "repo": spec["repo"],
            "quant": spec["quant"],
            "name": "Gemma 4 E4B " + spec["quant"],
            "model_id": "gemma-4-e4b-it-q8_0",
            "status": "queued",
            "done_bytes": 0,
            "total_bytes": 8_590_000_000,
            "percent": 0.0,
            "error": "",
            "trial": {},
            "files": [],
        }
        self.install_jobs_list = [job]
        return {"job": job}

    def install_jobs(self):
        return {"jobs": list(self.install_jobs_list)}

    def install_act(self, job_id, action):
        self.install_calls.append((action, job_id))
        job = next((j for j in self.install_jobs_list if j["id"] == job_id), self.install_jobs_list[0])
        status = {"pause": "paused", "resume": "queued", "trial": "trialwait"}.get(action, "cancelled")
        return {"job": {**job, "status": status}}

    def install_trial(self):  # «Пробный запуск» (34.5): those added, in the order added
        self.install_calls.append(("trial",))
        added = sorted((j for j in self.install_jobs_list if j["status"] == "added"), key=lambda j: j["created_at"])
        return {"jobs": [{**j, "status": "trialwait"} for j in added]}

    def install_trial_stop(self):  # those the list has as waiting go back
        self.install_calls.append(("trial-stop",))
        return {"jobs": [{**j, "status": "added"} for j in self.install_jobs_list if j["status"] == "trialwait"]}

    def install_forget(self, job_id):
        self.install_calls.append(("forget", job_id))
        return {"forgotten": job_id}

    def install_found(self):
        return json.loads(json.dumps(self.found))

    def install_scan(self):
        self.install_calls.append(("scan",))
        self.found["job"] = {"status": "scanning", "folders": 0, "files": 0, "drive": ""}
        return self.install_found()

    def install_scan_cancel(self):
        self.install_calls.append(("scan-cancel",))
        self.found["job"] = {"status": "cancelled"}
        return self.install_found()

    def install_local(self, path, projector="", name=""):
        self.install_calls.append(("local", path, projector, name))
        name = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".gguf")
        return {"job": {"id": "l1", "name": name, "model_id": "mine", "repo": "", "status": "queued", "files": [],
                        "own_files": 0, "total_bytes": 1, "done_bytes": 1}}  # fmt: skip

    def install_remove(self, model_id):
        self.install_calls.append(("remove", model_id))
        return {"job": {**self.install_jobs_list[0], "status": "removed"}}

    def sandbox(self, conversation_id):
        return {"projects": [dict(x) for x in self.sandboxed], "tasks": [dict(x) for x in self.task_sandboxes]}

    def chat_folder(self, conversation_id):  # Claude Code's folder in a chat with no project (31.7)
        found = getattr(self, "folders", {}).get(conversation_id)
        return dict(found) if found else {"path": "", "files": [], "count": 0, "truncated": False}

    def run_tasks(self, run_id):  # a coordinator's board (16.4); another run or a chat - none
        if run_id not in self.boards:
            raise HubError(f"no such run: {run_id}")
        return {"tasks": [dict(t) for t in self.boards[run_id]]}

    def apply_tasks(self, run_id, files=None, hunks=None):
        said = ("apply all", run_id) + ((files,) if files is not None or hunks is not None else ())
        self.sandbox_calls.append(said + ((hunks,) if hunks is not None else ()))
        self.task_sandboxes = []
        return {"applied": [{"task_id": "a", "project": "proj", "files": ["src/app.py"], "conflicts": []},
                            {"task_id": "b", "project": "proj", "files": ["src/app.py", "x.py"], "conflicts": []}],
                "kept": ["c"]}  # fmt: skip

    def discard_tasks(self, run_id):
        self.sandbox_calls.append(("discard all", run_id))
        self.task_sandboxes = []
        return {"discarded": ["a"]}

    def apply_sandbox(self, conversation_id, project_id, files=None, hunks=None):
        said = ("apply", conversation_id, project_id, files) + ((hunks,) if hunks is not None else ())
        self.sandbox_calls.append(said)
        self.sandboxed = []
        return {"applied": ["src/app.py"], "conflicts": ["README.md"], "kept": ["docs.md"] if files else []}

    def discard_sandbox(self, conversation_id, project_id):
        self.sandbox_calls.append(("discard", conversation_id, project_id))
        self.sandboxed = []
        return {"discarded": project_id}

    def agent_keys(self):
        return {"keys": [dict(k) for k in self.keys]}

    def set_agent_key(self, name, body, ticket):
        self._open(ticket)
        self.key_calls.append((name, body))
        self.keys = [k for k in self.keys if k["name"] != name] + [
            {"name": name, "host": body["host"], "header": body["header"], "prefix": body["prefix"], "set": True}
        ]
        return self.agent_keys()

    def delete_agent_key(self, name, ticket):
        self._open(ticket)
        self.keys = [k for k in self.keys if k["name"] != name]
        return self.agent_keys()

    def mail(self):
        return {"account": self.mailbox, "password": self.mailbox is not None}

    def set_mail(self, body, ticket):
        self._open(ticket)
        self.mail_calls.append(("set", body))
        self.mailbox = {"address": body["address"], "imap_host": "imap.yandex.ru", "imap_port": 993,
                        "smtp_host": "smtp.yandex.ru", "smtp_port": 465}  # fmt: skip
        return self.mail()

    def check_mail(self, ticket):
        self._open(ticket)
        return {"ok": True, "detail": "почта работает: входящие читаются, отправка доступна"}

    def delete_mail(self, ticket):
        self._open(ticket)
        self.mail_calls.append(("delete",))
        self.mailbox = None
        return self.mail()

    def schedules(self):
        return {"schedules": [dict(x) for x in self.scheduled], "quiet": dict(self.quiet)}

    def add_schedule(self, body):
        self.schedule_calls.append(("add", body))
        found = {"id": f"s{len(self.scheduled) + 1}", "title": body["title"], "goal": body["goal"],
                 "trigger": body["trigger"], "computer": body["computer"], "enabled": True,
                 "next_at": "2026-10-29T09:00", "waiting_until": None, "pending": [], "last_error": None,
                 "last_run_id": None}  # fmt: skip
        self.scheduled.append(found)
        return found

    def set_schedule(self, sid, **change):
        self.schedule_calls.append(("set", sid, change))
        found = next(x for x in self.scheduled if x["id"] == sid)
        found.update(change)
        return found

    def delete_schedule(self, sid):
        self.schedule_calls.append(("delete", sid))
        self.scheduled = [x for x in self.scheduled if x["id"] != sid]
        return {"removed": sid}

    def run_schedule(self, sid):
        self.schedule_calls.append(("run", sid))
        return {"run_id": "auto1"}

    def set_quiet(self, **change):
        if "from" in change and ":" not in change["from"]:
            raise HubError("quiet from: a time like 09:30")
        self.quiet = {**self.quiet, **change}
        return dict(self.quiet)

    def start_autonomous(self, body):
        self.auto_calls.append(("start", body))
        return {**AUTO_RUN, "id": "auto2", "conversation_id": "auto2", "goal": body["goal"]}

    def run_plan(self, run_id):
        return AUTO_PLAN if run_id == "auto1" else {**AUTO_PLAN, "plans": [], "questions": []}

    def retention(self):
        return {"days": 90, "max_gb": 2.0, "usage": {"snapshots": {"bytes": 12 * 1024**2, "files": 3},
                                                     "screens": {"bytes": 0, "files": 0},
                                                     "terminals": {"bytes": 3 * 1024**3, "files": 9}}}  # fmt: skip

    def set_retention(self, days, max_gb):
        self.log.append(("retention", days, max_gb))
        return {**self.retention(), "days": days, "max_gb": max_gb}

    def prune_kept(self):
        self.log.append(("prune",))
        return {**self.retention(), "removed": {"snapshots": 2, "screens": 0, "terminals": 1}}

    def answer_question(self, run_id, question_id, answer):
        self.auto_calls.append(("answer", run_id, question_id, answer))
        return {"id": question_id}

    def message_run(self, run_id, text):
        self.auto_calls.append(("message", run_id, text))
        waiting = run_id == "auto1" and not any(c[0] == "message" for c in self.auto_calls[:-1])
        return {"answered": "q1"} if waiting else {"noted": True}

    def set_autonomy(self, run_id, autonomy):
        self.auto_calls.append(("autonomy", run_id, autonomy))
        return {"autonomy": autonomy, "answered": []}

    def pause_run(self, run_id):
        self.auto_calls.append(("pause", run_id))

    def resume_run(self, run_id):
        self.auto_calls.append(("resume", run_id))

    def edit_run_plan(self, run_id, steps, reason):
        self.auto_calls.append(("plan", run_id, steps, reason))

    def export_model_settings(self):
        return self.exported

    def import_model_settings(self, data):
        self.imported.append(data)
        return {"saved": list(data.get("models") or {}), "skipped": [], "errors": {}}

    def export_settings(self):
        return {
            "kind": "localchat-settings",
            "version": 1,
            "exported_at": "2026-11-03T10:00:00Z",
            "redacted": 1,
            "projects": [{"path": r"C:\site", "name": "Сайт"}],
            "mcp": [{"name": "files", "target": "npx f"}],
        }

    def _settings_parts(self, data, parts, ticket):
        out = []
        for pid, title, behind in (("projects", "Проекты и их правила", False), ("mcp", "MCP-серверы", True)):
            if pid in data and (parts is None or pid in parts):
                item = {"what": data[pid][0].get("path") or data[pid][0]["name"], "action": "add", "detail": "",
                        "warn": "придёт выключенным" if behind else ""}  # fmt: skip
                out.append({"id": pid, "title": title, "locked": behind and (not ticket or ticket != self.ticket),
                            "items": [item]})  # fmt: skip
        return {"exported_at": data.get("exported_at", ""), "parts": out}

    def preview_settings(self, data, parts, ticket=""):
        self.settings_calls.append(("preview", parts, ticket))
        return self._settings_parts(data, parts, ticket)

    def import_settings(self, data, parts, ticket=""):
        self.settings_calls.append(("import", parts, ticket))
        return self._settings_parts(data, parts, ticket)

    def check_model(self, model_id):
        self.model_calls.append(("check", model_id))
        return {"ok": True, "text": "на связи", "ttft_s": 0.3, "tok_s": 88.0, "seconds": 1.2, "cost_usd": 0}

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

    def connections(self, ticket=""):
        unlocked = bool(ticket) and ticket == self.ticket
        rows = [
            {"name": "anthropic_api_key", "title": "Anthropic", "kind": "key", "purpose": "Модели Claude",
             "site": "https://console.anthropic.com/settings/keys", "hint": "sk-ant-…", "set": self.key,
             "tail": "-123" if self.key and unlocked else ""},
            {"name": "searxng_url", "title": "SearXNG", "kind": "address", "purpose": "Свой поисковик",
             "site": "https://docs.searxng.org/", "hint": "http://127.0.0.1:8888", "set": False, "value": ""},
            {"name": "telegram_bot_token", "title": "Telegram-бот", "kind": "token", "purpose": "С телефона",
             "site": "https://t.me/BotFather", "hint": "123456789:AA…", "set": False, "tail": ""},
        ]  # fmt: skip
        return {"password": self.password is not None, "unlocked": unlocked, "connections": rows,
                "telegram": {"linked": False}}  # fmt: skip

    def set_connections_password(self, password, old=""):
        if self.password is not None and old != self.password:
            raise HubError("неверный пароль", code="wrong_password")
        self.password, self.ticket = password, ""
        return {"password": True}

    def unlock_connections(self, password):
        if password != self.password:
            raise HubError("неверный пароль", code="wrong_password")
        self.ticket = "ticket-1"
        return self.ticket

    def lock_connections(self, ticket):
        self.secret_calls.append(("lock", ticket))
        self.ticket = ""

    def _open(self, ticket):
        if not ticket or ticket != self.ticket:
            raise HubError("подключения закрыты: введите пароль", code="locked")

    def mcp_servers(self):
        return {"servers": [dict(s) for s in self.mcp], "classes": ["read", "write"]}

    def add_mcp_server(self, name, target, env, ticket):
        self._open(ticket)
        self.mcp_calls.append(("add", name, target, env))
        server = {"name": name, "transport": "stdio", "target": target, "enabled": True, "state": "ready",
                  "detail": "", "env": [line.split("=")[0] for line in env.splitlines()],
                  "tools": [{"tool": "list_notes", "name": f"mcp__{name}__list_notes", "description": "Заметки.\nВсе.",
                             "class": "read", "default": "read"},
                            {"tool": "wipe_notes", "name": f"mcp__{name}__wipe_notes", "description": "",
                             "class": "destructive", "default": "destructive"}]}  # fmt: skip
        self.mcp.append(server)
        return server

    def set_mcp_server(self, name, ticket, **change):
        self._open(ticket)
        self.mcp_calls.append(("set", name, change))
        server = next(s for s in self.mcp if s["name"] == name)
        if "enabled" in change:
            server.update(enabled=change["enabled"], state="ready" if change["enabled"] else "off")
        return server

    def restart_mcp_server(self, name):
        self.mcp_calls.append(("restart", name))
        return next(s for s in self.mcp if s["name"] == name)

    def set_mcp_tool_class(self, name, tool, cls, ticket):
        self._open(ticket)
        self.mcp_calls.append(("class", name, tool, cls))
        server = next(s for s in self.mcp if s["name"] == name)
        next(t for t in server["tools"] if t["tool"] == tool)["class"] = cls
        return server

    def delete_mcp_server(self, name, ticket):
        self._open(ticket)
        self.mcp_calls.append(("delete", name))
        self.mcp = [s for s in self.mcp if s["name"] != name]
        return {"removed": name}

    def set_connection(self, name, value, ticket):
        self._open(ticket)
        self.secret_calls.append(("set", name, value))
        self.key = self.key or name == "anthropic_api_key"
        return {"name": name, "set": True, "catalog": {"synced": 2}}

    def delete_connection(self, name, ticket):
        self._open(ticket)
        self.secret_calls.append(("delete", name))
        self.key = self.key and name != "anthropic_api_key"
        return {"name": name, "set": False}

    def check_connection(self, name, ticket):
        self._open(ticket)
        return {"name": name, "ok": True, "detail": "ключ работает: моделей — 2"}

    def telegram_link(self, ticket):
        self._open(ticket)
        return {"code": "123456", "valid_s": 600}

    def provider_models(self, name):
        models = [
            {"id": "qwen/qwen3.8-coder", "name": "Qwen3.8 Coder", "input": 0.2, "output": 0.8, "context": 262144},
            {"id": "deepseek/deepseek-v4-pro", "name": "DeepSeek V4 Pro", "input": 0.66, "output": 1.98},
        ]
        return {"models": models, "chosen": list(self.chosen)}

    def choose_models(self, name, ids, ticket):
        self._open(ticket)
        self.chosen = list(ids)
        return {"chosen": self.chosen, "catalog": {"synced": 3}}

    def rename(self, cid, title):
        self.renamed.append((cid, title))
        return {"id": cid, "title": title}

    def create_conversation(self, title="", home_project=None):
        if home_project:  # a project's session (2026-10-28); an ordinary chat's making is not logged
            self.log.append(("create", home_project))
        return "c2"

    def goal(self, cid):  # a chat's goal (D44): none here
        return {"goal": None, "checks": [], "steering": [], "checking": False}

    def send(self, cid, text, model_id, effort, confirm_over_budget=False, files=None):
        if self.over_budget is not None and not (confirm_over_budget and self.over_budget):
            data = {"code": "budget_exceeded", "budgets": [REACHED], "can_confirm": self.over_budget}
            raise HubError("budget reached", code="budget_exceeded", data=data)
        self.sent.append((cid, text, model_id, effort, confirm_over_budget))
        self.log.append(("send", cid, text))
        if files:
            self.files_sent = files
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

    def card_export(self, pid):
        modes = {"claude": "create", "agents": "append", "cline": "unchanged"}
        paths = {"claude": "CLAUDE.md", "agents": "AGENTS.md", "cline": ".clinerules/localchat.md"}
        targets = [{"target": t, "path": paths[t], "mode": m, "exists": m != "create"} for t, m in modes.items()]
        return {"targets": targets, "block": "<!-- LocalChat -->"}

    def export_card(self, pid, targets):
        self.log.append(("export", pid, list(targets)))
        return {"files": [{"target": t} for t in targets]}

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
                "encoding": "utf-8", "sha256": sha(text), "eol": "CRLF" if "\r\n" in text else "LF"}  # fmt: skip

    def save_file(self, pid, path, text, encoding, base):
        """As the hub does it (14.1): the version the editing began from, else 409 with what is on the disk."""
        self.saves.append((pid, path, text, encoding, base))
        now = self.files.get(path)
        if (now is None and base) or (now is not None and sha(now) != base):
            reason = "gone" if now is None else "changed" if base else "exists"
            raise HubError("conflict", data={"reason": reason, "message": reason,
                                             "file": None if now is None else self.file(pid, path)})  # fmt: skip
        self.files[path] = text
        return {"path": path, "sha256": sha(text), "size": len(text.encode()), "encoding": encoding, "eol": "LF",
                "created": now is None, "change": None}  # fmt: skip

    def editor_unsaved(self, files):
        self.unsaved.append(files)
        return {"files": files}

    edit_answer = "    return sum(items)"  # the model's edit of a selection (14.7)
    edit_refused = None  # (reason, message): the hub's 409 instead

    def editor_edit(self, project_id, path, selected, before, after, instruction):
        self.edits = [*getattr(self, "edits", []), {"path": path, "selected": selected, "before": before,
                                                    "after": after, "instruction": instruction}]  # fmt: skip
        if self.edit_refused:
            raise HubError(self.edit_refused[1], data={"reason": self.edit_refused[0], "message": self.edit_refused[1]})
        return {"replacement": self.edit_answer, "model": "editor/fix"}

    complete_pieces = ("('ok')",)  # the model's grey text, as the hub streams it (14.6)
    complete_paused = None  # (reason, message): the hub's pause instead
    complete_delay = 0.0

    def complete(self, project_id, path, before, after, *, load=False, stop=lambda: False):
        asked = {"project_id": project_id, "path": path, "before": before, "after": after, "load": load,
                 "stopped": False}  # fmt: skip
        self.completions = [*getattr(self, "completions", []), asked]
        if self.complete_paused:
            yield {"paused": self.complete_paused[0], "message": self.complete_paused[1]}
            return
        yield {"model": "llama-swap/coder", "how": "infill"}
        for piece in self.complete_pieces:
            time.sleep(self.complete_delay)
            if stop():
                asked["stopped"] = True  # the window closed the stream: the hub would cancel its request
                return
            yield {"text": piece}
        yield {"done": True}

    def terminals(self, project_id=None):
        return [dict(t) for t in self.term_list if project_id in (None, t["project_id"])]

    def approvals(self):
        return [dict(a) for a in self.pending]

    def programs(self):
        return {"programs": [dict(p) for p in self.program_list], "scanning": False,
                "scanned_at": "2026-10-27T15:00:00Z", "error": "", "offer": self.program_offer}  # fmt: skip

    def scan_programs(self):
        self.program_calls.append(("scan",))
        return {"scanning": True}

    def decide_program(self, program_id, decision):
        self.program_calls.append(("decide", program_id, decision))
        for p in self.program_list:
            if p["id"] == program_id:
                p.update(decision=decision, fresh=False)
                return dict(p)
        raise HubError("no such program")

    def put_off_program(self, program_id):
        self.program_calls.append(("later", program_id))
        for p in self.program_list:
            if p["id"] == program_id:
                p["fresh"] = False
        return {}

    def add_program(self, path):
        self.program_calls.append(("add", path))
        program = {**PROGRAMS[0], "id": "pm", "name": "Portable", "kind": "manual", "exe": path, "fresh": False,
                   "decision": None, "decided_at": None}  # fmt: skip
        self.program_list.append(program)
        return dict(program)

    def remove_program(self, program_id):
        self.program_calls.append(("remove", program_id))
        self.program_list = [p for p in self.program_list if p["id"] != program_id]

    def dismiss_programs_offer(self):
        self.program_calls.append(("dismiss",))
        self.program_offer = None

    def advice(self, *, conversation_id=None, project_ids=None, priority="balanced"):
        self.advice_calls.append((conversation_id, project_ids, priority))  # apart from `log`: it comes often
        reasons = ["уже в видеопамяти", "102 т/с здесь", "бесплатно", "ещё"]
        return {"recommended": "llama-swap/coder", "priority": priority, "options": [
            {"model_id": "llama-swap/coder", "score": 0.8, "reasons": reasons, "blocked": None, "estimate_usd": 0},
            {"model_id": "anthropic/claude-opus-5-5", "score": 0.5, "reasons": [],
             "blocked": "«LocalChat» — только для локальных моделей (D10)", "estimate_usd": 0.03},
        ]}  # fmt: skip

    def agent_estimate(self, agent_id):
        return {"estimate_usd": 0.0042, "basis": "по 2 прошлым задачам этого агента"}

    def stats(self, days=30):
        return {
            "days": days,
            "models": [dict(STATS)],
            "spending": {
                "month_to_date_usd": 1.0,
                "daily_avg_7d_usd": 0.05,
                "forecast_month_usd": 1.23,
                "days_left": 4.5,
                "by_model_usd": {},
                "subscription_estimate_usd": 0.4,
            },
        }

    def agents(self):
        return {"agents": [dict(a) for a in self.agent_list], "tools": ["read_file", "edit_file", "run_command"]}

    def save_agent(self, body, agent_id=None):
        if not body["model_id"]:
            raise HubError("unknown model ''")
        self.log.append(("save_agent", agent_id, body))
        saved = {**AGENT, **body, "id": agent_id or "a2", "version": 2 if agent_id else 1}
        self.agent_list = [a for a in self.agent_list if a["id"] != saved["id"]] + [saved]
        return saved

    def delete_agent(self, agent_id):
        self.agent_list = [a for a in self.agent_list if a["id"] != agent_id]

    def start_run(self, agent_id, task, project_ids=None):
        self.log.append(("run", agent_id, task))
        run = {**RUN, "id": "r2", "conversation_id": "r2", "task": task, "status": "queued", "shown_status": "queued"}
        self.run_list.insert(0, run)
        return run

    def runs(self, limit=30):
        return [dict(r) for r in self.run_list]

    def cancel_run(self, run_id):
        self.log.append(("cancel_run", run_id))
        return {"cancelled": True}

    def changes(self, project_id):
        return [dict(c) for c in self.change_list]

    def rules(self, project_id):
        return [dict(r) for r in self.rule_list]

    def history(self, project_id, path=""):  # a project file's versions (stage 26): tests/test_history_ui.py
        return []

    def delete_rule(self, rule_id):
        self.rule_list = [r for r in self.rule_list if r["id"] != rule_id]

    def revert_change(self, change_id, *, force=False):
        if self.stale and not force:
            raise HubError("src/app.py has changed since: undoing would lose those changes")
        self.log.append(("revert", change_id, force))
        self.change_list = [{**c, "reverted_at": "2026-10-27T05:00:00Z"} for c in self.change_list]
        return self.change_list[0]

    def answer_approval(self, approval_id, *, allow, scope="once", comment="", hunks=None):
        if approval_id == "gone":
            raise HubError("no open approval gone: it was answered already, or never asked")
        self.answers.append((approval_id, allow, scope, comment, *([hunks] if hunks is not None else [])))
        self.pending = [a for a in self.pending if a["id"] != approval_id]
        return {"id": approval_id, "status": "allowed" if allow else "denied"}

    def unload_local(self):
        self.unloaded = getattr(self, "unloaded", 0) + 1
        return {"unloaded": True}

    def autostart(self):
        return dict(self.started)

    def set_autostart(self, **change):
        self.started.update(change)
        return dict(self.started)

    def release(self):
        return json.loads(json.dumps(self.release_state))

    def release_check(self):
        self.release_calls.append("check")
        return self.release()

    def release_download(self):
        self.release_calls.append("download")
        self.release_state["job"] = {"status": "downloading", "version": "0.2.0", "done": 0, "total": 88_000_000}
        return self.release()

    def release_cancel(self):
        self.release_calls.append("cancel")
        return self.release()

    def release_install(self):
        self.release_calls.append("install")
        self.release_state["job"] = {**self.release_state["job"], "status": "installing"}
        return self.release()

    def set_release_auto(self, on):
        self.release_calls.append(("auto", on))
        self.release_state["auto"] = on
        return self.release()

    def set_language(self, code):
        self.languages.append(code)
        return {"language": code}

    def claude_code(self):
        return {"installed": True, "command": ["claude.exe"], "detail": "", "running": []}

    def claude_code_terminal(self, project_id, cols=120, rows=30):
        info = {**TERMINAL, "id": f"t{len(self.term_list) + 1}", "project_id": project_id, "title": "Claude Code",
                "cols": cols, "rows": rows, "created_at": "2026-10-27T01:00:00.000Z"}  # fmt: skip
        self.term_list.append(info)
        self.claude_terminals.append(project_id)
        return dict(info)

    def start_terminal(self, project_id, *, cols, rows, path="", command=None):
        info = {**TERMINAL, "id": f"t{len(self.term_list) + 1}", "project_id": project_id, "cols": cols, "rows": rows,
                "created_at": "2026-10-27T01:00:00.000Z"}  # fmt: skip
        self.term_list.append(info)
        return dict(info)

    def terminal_output(self, terminal_id, since=None):
        self.output_asked.append(since)
        text, end = self.term_text, len(self.term_text)
        first = max(0, end - self.term_kept)
        start = since if since is not None and first <= since <= end else first
        return {"data": text[start:], "start": start, "end": end, "running": True}

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


def test_an_answer_the_hub_lost_does_not_keep_the_chat_busy(app):
    """The hub restarted (or stopped) mid-answer: no chat.done or chat.error ever comes. The chat was "answering"
    forever, sending stopped instead of sending, until the window restarted."""
    opened(app)
    app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
    app.on_event(event("chat.delta", kind="content", text="половина ответа"))
    app._render_live()
    assert app.busyNow
    app._on_connected(False)
    app._on_connected(True)  # back: the hub's status lists no running chat
    wait_until(lambda: not app.busyNow)
    wait_until(lambda: not app.bg.busy)  # the chat is read again, too: the mark survives that, whatever came first
    assert app.messages.find("live") == -1
    assert app.messages.rows[-1]["role"] == "error" and "прервался" in app.messages.rows[-1]["text"]
    assert sum(row["role"] == "error" for row in app.messages.rows) == 1
    assert not app.live and app._stale_runs == set() and app._interrupted == set()
    for status_first in (True, False):  # the status and the chat come back in either order
        app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
        app._stale_runs = {"c1"}
        steps = [lambda: app._show_status({**STATUS, "busy": []}),
                 lambda: app._show_conversation("c1", copy.deepcopy(HISTORY))]  # fmt: skip
        for step in steps if status_first else steps[::-1]:
            step()
        assert [row["role"] for row in app.messages.rows] == ["user", "assistant", "error"], status_first
        assert not app.busyNow and not app._interrupted

    app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
    app.on_event(event("chat.delta", kind="content", text="ещё"))
    app.refresh_status()  # a run that started after the break is the hub's: a status does not end it
    wait_until(lambda: not app.bg.busy)
    assert app.busyNow
    send(app, "hub.stopping", {})
    assert not app.busyNow and app.messages.find("live") == -1


def test_the_answer_after_a_tool_step_keeps_its_model(app):
    """The step with the tool calls is a message of its own; the answer that follows is the same run's: its
    author is that model, not a nameless cloud one."""
    opened(app)
    app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
    step = {"id": 3, "role": "assistant", "model_id": "llama-swap/coder",
            "content": [{"type": "tool_call", "id": "t1", "name": "read_file", "input": {"path": "a.py"}}]}  # fmt: skip
    app.on_event(event("chat.message", message=step))
    app.on_event(event("chat.delta", kind="content", text="дальше"))
    app._render_live()
    live = app.messages.rows[app.messages.find("live")]
    assert live["author"] == "Coder" and live["local"]


def test_parallel_calls_each_get_their_result(app):
    """Claude Code stores parallel calls as separate messages; one tool message answers them all, by id."""

    def call(mid, cid, path):
        block = {"type": "tool_call", "id": cid, "name": "read_file", "input": {"path": path}}
        return {"id": mid, "role": "assistant", "model_id": "llama-swap/coder", "content": [block]}

    results = {"id": 12, "role": "tool", "model_id": None, "content": [
        {"type": "tool_result", "id": "a", "name": "read_file", "output": "A", "error": False},
        {"type": "tool_result", "id": "b", "name": "read_file", "output": "B", "error": False}]}  # fmt: skip
    shown = rows.rows_for([call(10, "a", "a.py"), call(11, "b", "b.py"), results], {})
    assert [(t["state"], t["output"]) for row in shown for t in row["tools"]] == [("ok", "A"), ("ok", "B")]

    opened(app)  # the same live: each step's calls stay in their own row
    app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
    app.on_event(event("chat.message", message=call(10, "a", "a.py")))
    app.on_event(event("chat.message", message=call(11, "b", "b.py")))
    app.on_event(event("chat.message", message=results))
    states = [t["state"] for row in app.messages.rows for t in row.get("tools") or []]
    assert states == ["ok", "ok"]


def test_an_answer_cut_at_the_step_limit_says_so(app):
    """31.3 (live, 2026-11-08): `coder` building an app hit the step limit mid-work - the answer ended on a refused
    call, with no text, and the window said nothing. A card says so in plain words now, live and when the chat is
    opened again; an answer the model went on to give after the refusal needs none."""
    from hub.chat import STEP_LIMIT

    block = {"type": "tool_call", "id": "a", "name": "run_command", "input": {"command": "x"}}
    call = {"id": 10, "role": "assistant", "model_id": "llama-swap/coder", "content": [block]}
    refused = {"id": 11, "role": "tool", "model_id": None, "content": [
        {"type": "tool_result", "id": "a", "name": "run_command", "output": STEP_LIMIT, "error": True}]}  # fmt: skip
    shown = rows.rows_for([call, refused], {})
    assert [r["role"] for r in shown] == ["assistant", "error"]
    assert shown[-1]["title"] == "Модель остановилась на пределе шагов" and "продолжай" in shown[-1]["hint"]
    answer = {"id": 12, "role": "assistant", "model_id": "llama-swap/coder",
              "content": [{"type": "text", "text": "Готово"}]}  # fmt: skip
    assert [r["role"] for r in rows.rows_for([call, refused, answer], {})] == ["assistant", "assistant"]

    opened(app)  # live: chat.done says the answer was cut
    app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
    app.on_event(event("chat.message", message=call))
    app.on_event(event("chat.message", message=refused))
    app.on_event(event("chat.done", message_id=10, limited=True, usage={}, stats={}))
    last = app.messages.rows[-1]
    assert last["role"] == "error" and last["title"] == "Модель остановилась на пределе шагов"


def test_claude_codes_folder_shows_over_the_composer(app):
    """31.7 (the user's choice, 2026-11-08): what Claude Code made in a chat with no project lay in a folder the
    window never showed. A bar over the composer says how many files, the newest by name, and opens the folder;
    it is read again when an answer ends, and another chat has its own (or none)."""
    from app.ui import chat_folder

    folder = "C:/data/claude-code/chats/c1"
    files = [{"path": p, "size": 10} for p in ("site/index.html", "style.css", "README.md", "logo.svg")]
    app.client.folders = {"c1": {"path": folder, "files": files[:1], "count": 1, "truncated": False}}
    opened(app)
    wait_until(lambda: app.chat_folder.shown)
    assert app.chat_folder.summary == "Папка чата · 1 файл: index.html" and app.chat_folder.path == folder
    app.client.folders["c1"] = {"path": folder, "files": files, "count": 4, "truncated": False}
    app.on_event(event("chat.done", message_id=2, usage={}, stats={}))
    wait_until(lambda: "4 файла" in app.chat_folder.summary)
    assert app.chat_folder.summary == "Папка чата · 4 файла: index.html, style.css, README.md…"
    five = chat_folder.summary({"count": 5, "files": files})
    assert five == "Папка чата · 5 файлов: index.html, style.css, README.md…"
    app.chat_folder.copyPath()
    assert app._clipboard().text() == folder
    app.newChat()  # another chat: not this folder
    assert not app.chat_folder.shown


def test_a_new_message_is_not_a_chat_switch(app):
    """The line under the title changes with every message; only another chat resets the view's place (the
    reader scrolled up was pulled to the bottom by each step) and the composer's suggestions."""
    opened(app)
    switches = []
    app.currentChanged.connect(lambda: switches.append(1))
    user = {"id": 5, "role": "user", "model_id": None, "content": [{"type": "text", "text": "ещё вопрос"}]}
    app.on_event(event("chat.message", message=user))
    app.on_event(event("chat.titled", title="Новое имя"))
    assert switches == [] and app.currentTitle == "Новое имя" and app.currentMeta.startswith("3 сообщения")
    app.openChat("c2")
    assert switches == [1]


def test_a_slow_render_is_rendered_less_often(app, monkeypatch):
    """A long answer costs more to render: the next render waits RENDER_SHARE times as long, up to a limit, so the
    window never spends all its time on it (it froze on long answers)."""
    from app.ui import controller

    opened(app)
    app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
    app._render_live()
    assert app._render_timer.interval() == controller.RENDER_INTERVAL_MS  # a quick render: the usual pace
    monkeypatch.setattr(app, "_render_live_row", lambda: time.sleep(0.05))
    app._render_live()
    assert 4 * 50 <= app._render_timer.interval() < 4 * 50 + 100
    monkeypatch.setattr(app, "_render_live_row", lambda: time.sleep(0.4))
    app._render_live()
    assert app._render_timer.interval() == controller.RENDER_INTERVAL_MAX_MS


def test_the_apps_own_fonts_and_icons(qapp):
    """Inter and JetBrains Mono come with the app (nothing installed into Windows); every icon the window names
    exists and draws - Lucide's files, a few of the app's own."""
    import re
    from pathlib import Path

    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    from app.ui import icons
    from app.ui.theme import Theme, load_fonts

    assert {"Inter", "JetBrains Mono"} <= set(load_fonts())
    theme = Theme("dark")
    assert (theme.uiFont, theme.monoFont) == ("Inter", "JetBrains Mono")
    qml = "\n".join(p.read_text(encoding="utf-8") for p in Path("app/ui/qml").glob("*.qml"))
    named = set(re.findall(r"\b(?:name|iconName):\s*\"([a-z-]+)\"", qml))
    named |= {row[2] for row in commands.ACTIONS}
    assert named and named <= icons.NAMES, named - icons.NAMES
    for name in icons.NAMES:
        drawn = QSvgRenderer(QByteArray(icons.svg(name, "#ff0000").encode()))
        assert drawn.isValid(), name
    assert 'stroke-width="1.75"' in icons.svg("shield", "#ff0000") and "currentColor" not in icons.svg("key", "#1")


def test_code_colours_are_kept_per_theme():
    """Finished code blocks of a streamed answer are coloured once (a cache), but a theme switch colours anew."""
    from app import render

    first = render.highlight_code("def f():\n    return 1", "python")
    assert render.highlight_code("def f():\n    return 1", "python") is first  # the same string: from the cache
    before = palette.current()
    palette.use(palette.LIGHT if before is palette.DARK else palette.DARK)
    try:
        assert render.highlight_code("def f():\n    return 1", "python") != first
    finally:
        palette.use(before)
    assert all(set(s) == {"kind", "lang", "html", "code"} for s in rows.segments("text\n\n```py\nx\n```\n\nmore"))


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


def test_each_chat_keeps_its_draft_through_switches_and_a_restart(app, tmp_path):
    """The composer's text is the open chat's draft (D46): another chat has its own, a new chat too, and app.ini keeps
    them all - a crash or a restart loses at most the last moment of typing."""
    app.keepDraft("для нового чата")
    opened(app)
    assert app.draftFor("c1") == "" and app.draftFor("") == "для нового чата"
    app.keepDraft("про первый")
    app.newChat()
    assert app.draftFor(app.currentId) == "для нового чата" and app.draftFor("c1") == "про первый"
    wait_until(lambda: not app._drafts_timer.isActive())  # saved a moment after the last key
    again = Controller(data_paths().ensure(), QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat),
                       app.theme)  # fmt: skip
    assert again.draftFor("c1") == "про первый" and again.draftFor("") == "для нового чата"

    app.askDeleteChat("c1")  # a deleted chat's draft goes with it
    app.answer(True)
    wait_until(lambda: app.client.deleted == ["c1"])
    assert app.draftFor("c1") == ""

    app.keepDraft("")  # sent from a new chat: the composer let its text go, and the next words are typed at once
    app.send("вопрос")
    app.keepDraft("следующий")
    wait_until(lambda: app.current == "c2")
    assert app.draftFor("c2") == "следующий" and app.draftFor("") == ""  # they belong to the chat made for it


def test_the_composer_shows_each_chats_draft(qapp):
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        area = item_named(quick.window.contentItem(), "composerInput")
        area.setProperty("text", "начал писать")
        opened(app)
        assert area.property("text") == ""
        area.setProperty("text", "в первом чате")
        app.newChat()
        assert area.property("text") == "начал писать"
        app.openChat("c1")
        assert area.property("text") == "в первом чате"
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_chat_is_laid_out_off_the_windows_thread(app, monkeypatch):
    """Opening a chat made every message's Markdown and code into rich text on the window's thread: frozen for 0.7 s
    on 1000 messages (D46, step 13.7). The rows are made on the worker thread with the history; a theme switched
    meanwhile has them made again in its colours."""
    import threading

    where = []
    rows_for = rows.rows_for

    def made(*args):
        where.append(threading.current_thread() is threading.main_thread())
        return rows_for(*args)

    monkeypatch.setattr(rows, "rows_for", made)
    opened(app)
    assert where == [False]

    def made_then_switched(*args):
        built = made(*args)
        monkeypatch.setattr(palette, "_current", palette.LIGHT)  # the user switched the theme meanwhile
        return built

    monkeypatch.setattr(rows, "rows_for", made_then_switched)
    app.newChat()
    app.openChat("c1")
    wait_until(lambda: len(app.messages.rows) == 2)
    assert palette.LIGHT.text_soft in app.messages.rows[1]["segments"][0]["html"]


def test_the_language_switch_keeps_the_choice_tells_the_hub_and_opens_the_window_again(app):
    """Settings → «Язык · Language» (10.5): the choice is kept in app.ini, the hub hears it at once (it heard the
    window's language on connect too), and the window opens again - its texts are made as it starts."""
    from app.ui.translator import LANGUAGE_KEY

    assert app.language == "ru" and [(x["id"], x["name"]) for x in app.languages] == [("ru", "Русский"),
                                                                                       ("en", "English")]  # fmt: skip
    assert app.client.languages == ["ru"]  # told on connect
    again = []
    app.restartRequested.connect(lambda: again.append(True))
    app.setLanguage("ru")
    app.setLanguage("xx")
    assert again == [] and app.client.languages == ["ru"]  # the same language, or none AFO speaks: nothing
    app.setLanguage("en")
    assert app.settings.value(LANGUAGE_KEY) == "en" and app.client.languages == ["ru", "en"] and again == [True]


def test_afos_own_update_from_the_settings(app):
    """Settings → «О программе» (10.4d): a newer AFO said once in the status line, downloaded on a click, installed
    after a question - the window closes and opens again on the new version; a checkout is updated with git."""
    view = app.release_view
    assert view.text == "AFO 0.1.0" and not view.canDownload and not view.canInstall and view.auto
    newer = {
        "latest": "0.2.0",
        "newer": True,
        "size": 88_253_434,
        "checked": "2026-11-05T10:00:00Z",
        "url": "https://github.com/WorBHacker/AFO/releases/tag/v0.2.0",
    }
    app.client.release_state.update(newer)
    view.check()
    wait_until(lambda: view.canDownload)
    assert app.client.release_calls == ["check"] and view.text == "Есть AFO 0.2.0 (84,2 МБ) — у вас 0.1.0"
    assert view.notesUrl.endswith("/v0.2.0") and "0.2.0" in app._flash  # said once, in the status line
    app._set_flash("")
    view.load()
    wait_until(lambda: not app.bg.busy)
    assert app._flash == ""  # the same version is not said again
    view.download()
    wait_until(lambda: view.busy)
    assert view.text.startswith("Скачивается AFO 0.2.0") and not view.canDownload
    send(app, "release.changed", {"job": {"status": "downloading", "version": "0.2.0", "done": 44_000_000,
                                          "total": 88_000_000}})  # fmt: skip
    assert view.progress == 0.5
    ready = {"status": "ready", "version": "0.2.0", "done": 88_000_000, "total": 88_000_000, "file": "x.exe"}
    app.client.release_state["job"] = ready
    send(app, "release.changed", {"job": ready})
    wait_until(lambda: view.canInstall)
    view.install()
    assert app.question["confirm"] == "Установить" and "0.2.0" in app.question["title"]
    app.answer(True)
    wait_until(lambda: "install" in app.client.release_calls)
    view.setAuto(False)
    wait_until(lambda: ("auto", False) in app.client.release_calls)
    # just updated, and a newer one already out (live on 2026-11-05: «AFO обновлена до 0.1.2» hid 0.1.3)
    app.client.release_state["job"] = {"status": "updated", "version": "0.1.0", "from": "0.0.9"}
    view.load()
    wait_until(lambda: view.canDownload)
    assert view.text == "Есть AFO 0.2.0 (84,2 МБ) — у вас 0.1.0"
    # a checkout: the newer version is said, the installer is not offered
    app.client.release_state.update(installed=False, job={"status": "idle"})
    view.load()
    wait_until(lambda: not view.canInstall and "git pull" in view.note)
    assert not view.canDownload


def test_theme_switch_rerenders_the_html(app):
    """A row's HTML is rendered again in the new theme when the chat view asks for it - a row in sight (24.3)."""
    opened(app)
    app.setTheme("light")
    assert palette.current() is palette.LIGHT
    assert palette.LIGHT.text_soft not in app.messages.rows[1]["segments"][0]["html"]  # not in sight: not yet
    app.restyleRow(1)
    assert palette.LIGHT.text_soft in app.messages.rows[1]["segments"][0]["html"]
    app.restyleRow(1)  # once is enough
    assert app.theme.p["window"] == palette.LIGHT.window
    assert app.settings.value("ui/theme") == "light"


def test_a_theme_switch_renders_again_only_the_rows_in_sight(qapp, monkeypatch):
    """24.3: in a long chat a theme switch renders again the rows on screen and near it at once, the others when the
    scroll comes to them - all at once, 1000 messages held the window 0.66 s."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.setTheme("dark")  # what the switch goes from
        client = FakeClient()
        code = "\n".join(f"total += {n}" for n in range(12))
        messages = []
        for n in range(150):
            messages.append({"id": len(messages) + 1, "role": "user", "model_id": None, "usage": None,
                             "content": [{"type": "text", "text": f"вопрос {n}"}]})  # fmt: skip
            answer = f"Ответ {n}\n\n```python\n{code}\n```"
            messages.append({"id": len(messages) + 1, "role": "assistant", "model_id": "llama-swap/coder",
                             "usage": None, "content": [{"type": "text", "text": answer}]})  # fmt: skip
        client.histories["c1"] = {"id": "c1", "title": "Длинный", "busy": False, "messages": messages}
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openChat("c1")
        wait_until(lambda: len(app.messages.rows) == 300)
        for _ in range(30):
            QApplication.processEvents()
        made = []
        real = rows.restyled
        monkeypatch.setattr(rows, "restyled", lambda row: made.append(row["key"]) or real(row))
        app.setTheme("light")
        for _ in range(10):
            QApplication.processEvents()

        def light(index: int) -> bool:  # an answer's row: its words in the light theme's colour
            return palette.LIGHT.text_soft in app.messages.rows[index]["segments"][0]["html"]

        assert 0 < len(made) < 40 and light(299) and not light(1)  # the end in sight, the start not
        conversation = next(o for o in quick.window.findChildren(QObject)
                            if o.metaObject().className().startswith("ChatView"))  # fmt: skip
        QMetaObject.invokeMethod(conversation, "positionViewAtBeginning")
        for _ in range(10):
            QApplication.processEvents()
        assert light(1) and len(made) < 80
        assert quick.warnings == []
    finally:
        quick.close()
        quick.theme.setMode("dark")


def test_copy_puts_text_on_the_clipboard(app, clipboard_stand_in):
    app.copy("print(1)")
    assert clipboard_stand_in.text() == "print(1)"
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


def long_chat_with_tools(steps: int = 8) -> dict:
    """A chat like the user's: long answers with reasoning between short questions, steps with many tool calls."""
    messages: list[dict] = []
    for n in range(steps):
        messages.append({"id": len(messages) + 1, "role": "user", "model_id": None, "usage": None,
                         "content": [{"type": "text", "text": f"вопрос {n}"}]})  # fmt: skip
        calls = [{"type": "tool_call", "id": f"c{n}-{k}", "name": "web_fetch", "input": {"url": f"http://x/{k}"}}
                 for k in range(2 + n % 4)]  # fmt: skip
        messages.append({"id": len(messages) + 1, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
                         "content": [{"type": "reasoning", "text": "думаю " * 200}, *calls]})  # fmt: skip
        messages.append({"id": len(messages) + 1, "role": "tool", "model_id": None, "usage": None,
                         "content": [{"type": "tool_result", "id": c["id"], "name": "web_fetch",
                                      "output": "страница\n" * (5 + n), "error": False} for c in calls]})  # fmt: skip
        body = "\n\n".join(f"Абзац {k}: " + "текст ответа, " * (10 + 25 * (n % 3)) for k in range(1 + n % 5))
        if n % 2:
            body += "\n\n```python\n" + "print('строка')\n" * (3 + 8 * n) + "```"
        messages.append({"id": len(messages) + 1, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
                         "content": [{"type": "text", "text": body}]})  # fmt: skip
    return {"id": "c1", "title": "Длинный", "busy": False, "messages": messages}


def test_open_reasoning_streams_in_parts_that_add_up_to_it(qapp):
    """Open reasoning shows in parts cut at line ends while it streams, so a tick lays out only the last part (one
    TextEdit of 100k characters laid all of it out on every tick). Read back, the parts are exactly the reasoning.
    Finished, it is one text - a selection goes through it (24.2)."""
    import random

    from PySide6.QtCore import QMetaObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)

        def texts() -> list:
            quick.window.grabWindow()  # a frame: the list lays out (and creates) the rows it shows
            todo, found = [quick.window.contentItem()], []
            while todo:
                item = todo.pop(0)
                todo.extend(item.childItems())
                if item.objectName() == "thinkingParts":
                    found.append([child for child in item.childItems() if child.property("part") is not None])
            return next(p for p in found if p and p[0].property("text").startswith("Старт"))

        def parts() -> list[str]:
            return [item.property("text") for item in texts()]

        app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
        app.on_event(event("chat.delta", kind="reasoning", text="Старт.\n"))
        app._render_live()
        app.toggleThinking(app.messages.find("live"))
        rng, sent = random.Random(3), "Старт.\n"
        for n in range(120):  # lines, blank lines, and a stretch with no line end at all
            piece = rng.choice(["строка рассуждения " * rng.randint(1, 30), "\n", "\n\n", "x" * 900]) + rng.choice(
                ["", "\n"]
            )
            sent += piece
            app.on_event(event("chat.delta", kind="reasoning", text=piece))
            if n % 7 == 0:
                app._render_live()
                quick.window.grabWindow()
        app._render_live()
        for _ in range(5):
            QApplication.processEvents()
        shown = parts()
        assert len(shown) > 3 and "\n".join(shown) == sent
        live = app.messages.find("live")
        app.toggleThinking(live)  # folded and opened again: built anew, the same text
        app.toggleThinking(live)
        for _ in range(5):
            QApplication.processEvents()
        assert "\n".join(parts()) == sent

        # the answer begins: the reasoning is finished - one text, selected through (24.2)
        app.on_event(event("chat.delta", kind="text", text="Ответ."))
        app._render_live()
        for _ in range(5):
            QApplication.processEvents()
        assert parts() == [sent]
        body = next(item for item in texts() if item.property("text") == sent)
        QMetaObject.invokeMethod(body, "selectAll")
        assert body.property("selectedText") == sent

        # finished but longer than WHOLE: still in parts (laid out at once, 100 000 characters took 13 ms)
        long = ("строка рассуждения " * 20 + "\n") * 400
        assert len(long) > 100_000
        app.client.histories["c2"] = {"id": "c2", "title": "Длинное", "busy": False, "messages": [
            {"id": 1, "role": "user", "model_id": None, "usage": None, "content": [{"type": "text", "text": "?"}]},
            {"id": 2, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
             "content": [{"type": "reasoning", "text": "Старт.\n" + long}, {"type": "text", "text": "Ответ."}]},
        ]}  # fmt: skip
        app.openChat("c2")
        wait_until(lambda: len(app.messages.rows) == 2)
        app.toggleThinking(1)
        for _ in range(5):
            QApplication.processEvents()
        shown = parts()
        assert len(shown) > 10 and "\n".join(shown) == "Старт.\n" + long
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_window_rests_while_an_answer_waits_and_after_it(qapp):
    """24.5: the window draws only when something changes. The pulsing dots were endless animations: the window drew
    ~60 frames a second all through an answer (a dot steps twice a second now). And after an answer in a long chat it
    went on drawing: the list's highlight - unused, but there - crept towards its current row (the first) across
    estimated heights that the answer had moved."""
    from app.ui.main import QuickApp
    from tests.stress.test_chat import answer

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        quick.window.resize(2560, 1400)
        app = quick.controller
        client = FakeClient()
        messages = []
        for n in range(500):
            messages.append({"id": len(messages) + 1, "role": "user", "model_id": None, "usage": None,
                             "content": [{"type": "text", "text": f"Вопрос {n}"}]})  # fmt: skip
            messages.append({"id": len(messages) + 1, "role": "assistant", "model_id": "llama-swap/coder",
                             "usage": None, "content": [{"type": "text", "text": answer(n)}]})  # fmt: skip
        client.histories["c1"] = {"id": "c1", "title": "Длинный", "busy": False, "messages": messages}
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openChat("c1")
        wait_until(lambda: len(app.messages.rows) == 1000, timeout=30)
        swapped = []
        quick.window.frameSwapped.connect(lambda: swapped.append(1))

        def frames_in_a_second() -> int:
            for _ in range(50):
                QApplication.processEvents()
                time.sleep(0.01)
            swapped.clear()
            began = time.monotonic()
            while time.monotonic() - began < 1:
                QApplication.processEvents()
                time.sleep(0.002)
            return len(swapped)

        assert frames_in_a_second() == 0
        app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
        app.on_event(event("chat.delta", kind="content", text="Ответ идёт"))
        app._render_live()
        assert frames_in_a_second() <= 5  # the answer waits: the sidebar's dot steps
        app.on_event(event("chat.done", message_id=1001))
        app._render_live()
        assert frames_in_a_second() <= 2
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_long_chat_with_tools_rests_at_its_end(qapp):
    """An open chat does nothing while nothing happens. Its rows have very different heights, and those out of view
    were estimates: following the end through positionViewAtEnd() on every contentHeight change moved them, and
    the view re-laid itself out forever (the window ~95% busy, the reader's place drifting)."""
    from PySide6.QtCore import Q_RETURN_ARG, QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.conversations_by_id = {"c1": long_chat_with_tools()}
        client.conversation = lambda cid: copy.deepcopy(client.conversations_by_id["c1"])
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openChat("c1")
        wait_until(lambda: len(app.messages.rows) > 10)
        view = next(o for o in quick.window.findChildren(QObject) if o.metaObject().className().startswith("ChatView"))

        def gap() -> float:
            direct = Qt.ConnectionType.DirectConnection
            return QMetaObject.invokeMethod(view, "endGap", direct, Q_RETURN_ARG("QVariant"))

        for _ in range(50):
            QApplication.processEvents()
        rest = view.property("contentY")
        for _ in range(50):
            QApplication.processEvents()
            time.sleep(0.002)
        assert view.property("contentY") == rest and abs(gap()) <= 1 and view.property("follow")

        # tool results: a folded one is not laid out, and opening one leaves the other calls' items alone
        import shiboken6

        def texts() -> dict[str, list[int]]:
            found: dict[str, list[int]] = {}
            todo = [quick.window.contentItem()]
            while todo:
                item = todo.pop()
                todo.extend(item.childItems())
                text = item.property("text")
                if isinstance(text, str) and text:
                    found.setdefault(text, []).append(shiboken6.getCppPointer(item)[0])
            return found

        last_step = max(i for i, row in enumerate(app.messages.rows) if row.get("tools"))
        labels = [tool["label"] for tool in app.messages.rows[last_step]["tools"]]
        before = texts()
        output = "страница\nстраница"  # a result itself, not its one-line summary
        assert not any(output in text for text in before)  # every result folded: no output text at all
        app.toggleTool(last_step, 1)
        for _ in range(10):
            QApplication.processEvents()
        after = texts()
        assert sum(output in text for text in after) == 1
        assert after[labels[0]] == before[labels[0]]  # the first call's line is the same item
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_streamed_answer_redraws_only_its_last_piece(qapp):
    """While an answer streams, the pieces it already finished stay the same items: rebuilding every piece on each
    render made a long answer freeze the window."""
    import shiboken6

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)

        def pieces() -> dict[str, int]:
            """The answer's pieces by a word in them -> their C++ object (a list's rows have no QObject parent, so
            the visual tree is walked)."""
            found, todo = {}, [quick.window.contentItem()]
            while todo:
                item = todo.pop()
                todo.extend(item.childItems())
                html = item.property("html") if item.metaObject().className().startswith(("Prose", "CodeBlock")) else ""
                for mark in ("Сначала", "streamed", "потом"):
                    if html and mark in html:
                        found[mark] = shiboken6.getCppPointer(item)[0]
            return found

        app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
        app.on_event(event("chat.delta", kind="content", text="Сначала код:\n\n```js\nlet streamed = 1\n```\n\nпотом"))
        wait_until(lambda: len(app.messages.rows[-1]["segments"]) == 3)
        wait_until(lambda: len(pieces()) == 3)
        before = pieces()
        app.on_event(event("chat.delta", kind="content", text=" ещё текст"))
        wait_until(lambda: "ещё текст" in app.messages.rows[-1]["segments"][-1]["html"])
        for _ in range(10):
            QApplication.processEvents()
        after = pieces()
        assert after["Сначала"] == before["Сначала"] and after["streamed"] == before["streamed"]
        assert quick.warnings == []
    finally:
        quick.close()


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
    assert asked == ["budgets", "connections"] and app.modelId == "anthropic/claude-opus-5-5"
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


def test_connections_behind_the_password(app):
    conns = app.connections
    wait_until(lambda: conns.state == "new")  # no password yet: the sheet asks to make one up
    conns.createPassword("12345", "12345")
    assert conns.error == "Пароль — не короче 6 символов"
    conns.createPassword("секрет-1", "секрет-2")
    assert conns.error == "Пароли не совпадают"
    conns.createPassword("секрет-1", "секрет-1")
    wait_until(lambda: conns.state == "open")
    assert app.keySet is False

    conns.save("anthropic_api_key", "  sk-ant-test  ")
    wait_until(lambda: app.keySet)
    assert app.client.secret_calls == [("set", "anthropic_api_key", "sk-ant-test")]
    wait_until(lambda: conns.items[0]["status"] == "задан · …-123")
    conns.check("anthropic_api_key")
    wait_until(lambda: conns.items[0]["note"] == "ключ работает: моделей — 2")
    assert conns.items[0]["noteOk"] is True and conns.items[1]["secret"] is False
    conns.linkTelegram()
    wait_until(lambda: conns.linkCode == "123456")
    conns.remove("anthropic_api_key")
    assert app.question["title"] == "Удалить подключение «Anthropic»?"
    app.answer(True)
    wait_until(lambda: not app.keySet)

    conns.lock()  # the sheet closed
    assert conns.state == "locked" and conns.linkCode == ""
    wait_until(lambda: ("lock", "ticket-1") in app.client.secret_calls)  # the hub forgets the ticket too
    conns.unlock("не тот")
    wait_until(lambda: conns.error == "неверный пароль")
    conns.unlock("секрет-1")
    wait_until(lambda: conns.state == "open" and conns.error == "")
    app.client.ticket = "lapsed"  # ten minutes without a change: the hub forgot the ticket
    conns.save("anthropic_api_key", "sk-ant-again")
    wait_until(lambda: conns.state == "locked")
    assert conns.error == "Подключения закрылись: введите пароль снова"


def test_autostart(app):
    wait_until(lambda: app.autostart.get("llama_swap") is True)
    app.setAutostart("login", True)
    wait_until(lambda: app.autostart["login"] is True)
    assert app.client.started["login"] is True
    app.setAutostart("nonsense", True)  # ignored
    assert "nonsense" not in app.client.started


def test_connection_rows():
    from app.ui.connections import connection_rows

    data = {"connections": [
        {"name": "a", "title": "A", "kind": "key", "purpose": "", "site": "", "hint": "", "set": True, "tail": ""},
        {"name": "u", "title": "U", "kind": "address", "purpose": "", "site": "", "hint": "", "set": True,
         "value": "http://127.0.0.1:8888"},
        {"name": "t", "title": "T", "kind": "token", "purpose": "", "site": "", "hint": "", "set": False},
        {"name": "v", "title": "V", "kind": "file", "purpose": "", "site": "", "hint": "", "set": True, "tail": "cafe"},
    ]}  # fmt: skip
    shown = connection_rows(data, {"t": {"ok": False, "text": "Telegram: Unauthorized"}})
    assert [r["status"] for r in shown] == ["задан", "http://127.0.0.1:8888", "не задан", "задан · …cafe"]
    assert shown[2]["note"] == "Telegram: Unauthorized" and shown[2]["noteOk"] is False
    assert [r["file"] for r in shown] == [False, False, False, True] and shown[3]["secret"] is True


def test_a_key_file_goes_to_the_hub_as_it_is(app, tmp_path):
    conns = app.connections
    wait_until(lambda: conns.state == "new")
    conns.createPassword("секрет-1", "секрет-1")
    wait_until(lambda: conns.state == "open")
    key_file = tmp_path / "project-1234-0123456789ab.json"
    key_file.write_text('{\n  "type": "service_account"\n}\n', encoding="utf-8")
    conns.saveFile("vertex_service_account", key_file.as_uri())
    wait_until(lambda: app.client.secret_calls)
    assert app.client.secret_calls == [("set", "vertex_service_account", '{\n  "type": "service_account"\n}')]
    conns.saveFile("vertex_service_account", (tmp_path / "gone.json").as_uri())
    assert conns.error.startswith("Файл не прочитан") and len(app.client.secret_calls) == 1
    (tmp_path / "big.json").write_text("x" * 70_000, encoding="utf-8")
    conns.saveFile("vertex_service_account", (tmp_path / "big.json").as_uri())
    assert conns.error == "Это не файл ключа: он больше 64 КБ" and len(app.client.secret_calls) == 1

    from hub.gateway.vertex_provider import NO_ROLE

    app.client.check_connection = lambda name, ticket: {"name": name, "ok": False, "detail": f"vertex: {NO_ROLE}"}
    conns.check("vertex_service_account")  # the hub's words, and under them what to do
    note = lambda: next(r for r in conns.items if r["name"] == "anthropic_api_key")["note"]  # noqa: E731
    wait_until(lambda: "Предоставить доступ" in conns._notes.get("vertex_service_account", {}).get("text", ""))
    assert conns._notes["vertex_service_account"]["text"].startswith(f"vertex: {NO_ROLE}\n") and note() == ""


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


def item_named(root, name):
    """An item below `root` by objectName, through the visual tree (a Repeater's items are not QObject children)."""
    for child in root.childItems():
        if child.objectName() == name:
            return child
        if (found := item_named(child, name)) is not None:
            return found
    return None


def test_the_palette_sheets_and_rename_dialog_open_without_warnings(qapp):
    from PySide6.QtCore import QMetaObject, QObject
    from PySide6.QtQuick import QQuickItem

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        wait_until(lambda: app.approvals.count == 1)
        assert quick.window.findChild(QObject, "approvalsButton").property("visible")  # the shield, with its count
        sample = data_paths().root / "settings.json"  # a settings file being looked at: its parts and lines drawn
        sample.write_text(json.dumps(app.client.export_settings()), encoding="utf-8")
        assert app.transfer_view.openFile(sample.as_uri())
        wait_until(lambda: app.transfer_view.parts)
        for name in (
            "palette",
            "budgetsSheet",
            "settingsSheet",
            "settingsLook",
            "settingsModels",
            "settingsAgent",
            "settingsStartup",
            "settingsHub",
            "settingsTransfer",
            "settingsAbout",
            "renameDialog",
            "approvalsList",
            "agentsSheet",
            "connectionsSheet",
            "transferSheet",
        ):
            popup = quick.window.findChild(QObject, name)
            assert popup is not None, name
            QMetaObject.invokeMethod(popup, "open")
            wait_until(lambda p=popup: p.property("opened"))  # after the enter animation
            if name == "palette":
                items = popup.property("items")  # a QJSValue or already a list, depending on how it was set
                assert len(items.toVariant() if hasattr(items, "toVariant") else items) > 4
            if name == "approvalsList":  # who waits: the open chat, its card on top with its answers
                wait_until(lambda p=popup: item_named(p.property("contentItem"), "waiting_c1") is not None)
                card = quick.window.findChild(QQuickItem, "chatApprovalCard")
                assert item_named(card, "approveOnce") is not None
                assert item_named(card, "approveChat").property("visible")
            if name == "agentsSheet":  # the profile in the editor, its run in the list
                wait_until(lambda: app.agents.runs)
                assert quick.window.findChild(QQuickItem, "agentName").property("text") == "Ревьюер"
                assert item_named(quick.window.findChild(QQuickItem, "agentRuns"), "") is not None
            QMetaObject.invokeMethod(popup, "close")
            for _ in range(20):
                QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()
        quick.theme.setMode("dark")


def test_the_card_on_top_of_the_chat(qapp):
    """The open chat's requests (D38): a card over the conversation, "1 из N", the next after an answer; a comment
    being typed stays while other requests come and go; the keys; "Показать всё"; the shield's list."""
    from PySide6.QtCore import QEvent, QMetaObject, QObject
    from PySide6.QtGui import QKeyEvent

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def drawn(condition) -> None:
        """Offscreen, frames drive the card's slide and the list's fade: draw them while waiting."""
        for _ in range(150):
            quick.window.grabWindow()
            QApplication.processEvents()
            if condition():
                return
            time.sleep(0.02)
        assert condition()

    def key(item, code, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(item, QKeyEvent(kind, code, modifiers))

    try:
        app = quick.controller
        client = FakeClient()
        diff = "\n".join(["--- a/x", "+++ b/x", "@@ -1,30 +1,30 @@"] + [f"+line {n}" for n in range(30)])
        client.pending = [{**APPROVAL, "preview": {"kind": "diff", "note": "", "text": diff}},
                          {**APPROVAL, "id": "ap2", "tool": "Bash", "kind": "command", "input": {"command": "pytest"},
                           "preview": {"kind": "command", "text": "pytest"}}]  # fmt: skip
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.approvals.count == 2)
        banner = quick.window.findChild(QObject, "approvalBanner")
        assert not banner.property("visible")  # no chat open yet
        opened(app)
        drawn(lambda: banner.property("opacity") == 1)
        card = quick.window.findChild(QObject, "chatApprovalCard")
        assert quick.window.findChild(QObject, "approvalPosition").property("text").startswith("1 из 2")
        lines = quick.window.findChild(QObject, "approvalLines")
        shown = lambda: sum(1 for c in lines.childItems() if c.property("text") is not None)  # noqa: E731
        assert shown() == 12  # a few lines; all 33 on "Показать всё"
        quick.window.findChild(QObject, "approvalShowAll").linkActivated.emit("x")
        drawn(lambda: shown() == 33)
        assert card.property("activeFocus")  # nobody was typing: the card has the keyboard

        comment = item_named(card, "approvalComment")
        key(card, Qt.Key.Key_Escape)  # Esc: to the comment
        assert comment.property("activeFocus")
        comment.setProperty("text", "сначала линтер")
        key(comment, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # never allows from the comment
        later = {**APPROVAL, "id": "ap9", "conversation_id": "other"}
        app.on_event({"type": "approval.requested", "payload": {"approval": later}})  # another chat's comes
        app.on_event({"type": "approval.requested", "payload": {"approval": {**later, "id": "ap10",
                                                                              "conversation_id": "c1"}}})  # fmt: skip
        drawn(lambda: quick.window.findChild(QObject, "approvalPosition").property("text").startswith("1 из 3"))
        assert comment.property("text") == "сначала линтер" and client.answers == []
        key(comment, Qt.Key.Key_Return)  # Enter in the comment: denied with it
        wait_until(lambda: client.answers == [("ap1", False, "once", "сначала линтер")])
        drawn(lambda: item_named(card, "approvalTitle").property("text") == "Claude Code: Выполнить команду")
        assert comment.property("text") == "" and quick.window.findChild(QObject, "approvalShowAll") is not None
        assert not quick.window.findChild(QObject, "approvalShowAll").property("visible")  # one line: nothing more

        card.forceActiveFocus()
        key(card, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # Ctrl+Enter: allowed once
        wait_until(lambda: client.answers[-1] == ("ap2", True, "once", ""))
        rm = {**APPROVAL, "id": "ap11", "conversation_id": "c1", "tool": "run_command", "kind": "command",
              "destructive": True, "input": {"command": "rm -rf build"},
              "preview": {"kind": "command", "text": "rm -rf build"}}  # fmt: skip
        app.approvals.answer("ap10", True, "chat", "")
        app.on_event({"type": "approval.requested", "payload": {"approval": rm}})
        drawn(lambda: app.approvals.card.get("id") == "ap11")
        card.forceActiveFocus()
        key(card, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # destructive: only its button allows
        for _ in range(10):
            QApplication.processEvents()
        assert all(a[0] != "ap11" for a in client.answers)
        assert not item_named(card, "approveChat").property("visible")

        # the shield's list: who waits elsewhere; a row opens that chat, from the projects mode too
        app.setMode("projects")
        drawn(lambda: banner.property("opacity") == 0)
        client.pending = [later, rm]  # the hub's queue now: the list reloads it on opening
        QMetaObject.invokeMethod(quick.window.findChild(QObject, "approvalsList"), "open")
        listed = quick.window.findChild(QObject, "approvalsList")
        drawn(lambda: listed.property("opened") and item_named(listed.property("contentItem"), "waiting_other"))
        item_named(listed.property("contentItem"), "waiting_other").clicked.emit()
        assert app.mode == "chats" and app.current == "other"
        drawn(lambda: not listed.property("visible") and app.approvals.card.get("id") == "ap9")
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_write_allowed_in_part_on_the_card(qapp):
    """17.3 (D51): a write of several hunks shows each with a tick; a digit ticks one from the keyboard; unticked,
    the button says «Разрешить отмеченное (N из M)», a part is allowed this once only (no «for the chat»), and the
    comment goes with it - the agent reads why the rest was left out."""
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtGui import QKeyEvent

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def key(item, code, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(item, QKeyEvent(kind, code, modifiers))

    def settle() -> None:
        for _ in range(10):
            quick.window.grabWindow()
            QApplication.processEvents()

    try:
        app = quick.controller
        client = FakeClient()
        client.pending = [{**APPROVAL, "preview": PARTED}]
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.approvals.count == 1)
        opened(app)
        card = quick.window.findChild(QObject, "chatApprovalCard")
        wait_until(lambda: item_named(card, "approvalHunk_2") is not None)
        allow = item_named(card, "approveOnce")
        assert allow.property("text") == "Разрешить" and item_named(card, "approveChat").property("visible")
        card.forceActiveFocus()
        key(card, Qt.Key.Key_2)  # the second hunk off
        settle()
        assert app.approvals.card["ticked"] == 2 and allow.property("text") == "Разрешить отмеченное (2 из 3)"
        assert not item_named(card, "approveChat").property("visible")  # a part: this once only
        item_named(card, "approvalHunk_1").clicked.emit()  # and on again by its tick
        settle()
        assert app.approvals.card["ticked"] == 3 and item_named(card, "approveChat").property("visible")
        key(card, Qt.Key.Key_2)
        settle()
        item_named(card, "approvalComment").setProperty("text", "x на 20-й строке не трогай")
        card.forceActiveFocus()
        key(card, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        wait_until(lambda: client.answers == [("ap1", True, "once", "x на 20-й строке не трогай", [0, 2])])
        for n in (1, 2, 3):
            app.approvals.tickHunk("ap1", n - 1)  # an answered request's ticks: gone with it
        assert app.approvals._skipped == {}

        client.pending = [{**APPROVAL, "id": "ap5", "preview": PARTED}]
        app.approvals.load()
        wait_until(lambda: app.approvals.card.get("id") == "ap5")
        for n in range(3):
            app.approvals.tickHunk("ap5", n)
        settle()
        assert app.approvals.card["ticked"] == 0 and not item_named(card, "approveOnce").property("enabled")
        app.approvals.answer("ap5", True, "once", "")  # nothing ticked: nothing to allow
        settle()
        assert len(client.answers) == 1 and app.approvals.count == 1
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_request_with_no_chat_is_answered_in_the_list(qapp):
    """An outside MCP client's request has no chat: it is a card in the shield's list, and a comment typed in one
    such card stays while another is answered or arrives (each card is its own row)."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.pending = [{**APPROVAL, "conversation_id": None, "client": "cline"},
                          {**APPROVAL, "id": "ap2", "conversation_id": None, "client": "cline", "tool": "Bash",
                           "kind": "command", "input": {"command": "pytest"},
                           "preview": {"kind": "command", "text": "pytest"}}]  # fmt: skip
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.approvals.looseCount == 2)
        listed = quick.window.findChild(QObject, "approvalsList")
        QMetaObject.invokeMethod(listed, "open")
        wait_until(lambda: listed.property("opened"))

        def fields() -> list:
            found, todo = [], [quick.window.findChild(QObject, "looseApprovals")]
            while todo:
                item = todo.pop(0)
                todo.extend(item.childItems())
                if item.objectName() == "approvalComment":
                    found.append(item)
            return found

        wait_until(lambda: len(fields()) == 2)
        fields()[1].setProperty("text", "сначала линтер")
        app.approvals.answer(APPROVAL["id"], True, "once", "")  # the first card goes
        later = {**APPROVAL, "id": "ap9", "conversation_id": None}
        app.on_event({"type": "approval.requested", "payload": {"approval": later}})  # a new one comes
        for _ in range(10):
            QApplication.processEvents()
        assert [f.property("text") for f in fields()] == ["сначала линтер", ""]
        assert not item_named(quick.window.findChild(QObject, "looseApprovals"), "approveChat").property("visible")
        assert quick.warnings == []
    finally:
        quick.close()


# --- step 9.6a: the programs the agent may control (D43) ---


def test_model_setup_fields_back_and_forth():
    from app.ui import model_setup as ms

    settings = {"hidden": True, "name": "Кодер", "capabilities": {"context": 65536, "tools": False},
                "prices": {"input": 1.5}, "params": {"temperature": 0.2, "top_k": 20, "stop": ["###", "END"],
                                                     "thinking": False}, "preset": "precise", "effort": "high",
                "system_prompt": "Кратко."}  # fmt: skip
    draft = ms.draft_of(settings)
    assert (draft["context"], draft["tools"], draft["vision"], draft["temperature"], draft["top_k"]) == (
        "65536", "no", "", "0.2", "20")  # fmt: skip
    assert draft["stop"] == "###\nEND" and draft["thinking"] == "off" and draft["price_input"] == "1.5"
    every = ["max_tokens", "min_p", "repeat_penalty", "stop", "temperature", "thinking", "top_k", "top_p"]
    assert ms.settings_of(draft, every) == settings  # there and back unchanged
    assert ms.settings_of({**draft, "temperature": "0,7"}, every)["params"]["temperature"] == 0.7  # a comma counts
    assert "temperature" not in ms.settings_of(draft, ["max_tokens", "stop"])["params"]  # Claude takes none
    for field, value, said in (("temperature", "abc", "не число"), ("top_k", "2.5", "целое")):
        with pytest.raises(ValueError, match=said):
            ms.settings_of({**draft, field: value}, every)
    assert ms.settings_of({**draft, "system_prompt": "  Всем.  "}, [], general=True) == {"system_prompt": "Всем."}
    assert ms.settings_of(dict(ms.BLANK), every) == {}  # nothing set: everything as the provider says
    models = [{**MODELS[0], "provider": "anthropic", "hidden": True, "settings_version": 2, "provider_name": "Opus"},
              {**MODELS[1], "provider": "llama-swap"}]  # fmt: skip
    items = ms.choices(models, "llama-swap/coder")
    assert [i.get("section") or i["name"] for i in items] == [
        "Все модели — общий системный промпт",
        "Локальные · llama-swap",
        "Coder",
        "Claude",
        "Claude Opus 5.5",
    ]
    assert items[4]["note"] == "скрыта в выборе · своё имя, у поставщика «Opus» · настройки v2" and items[2]["selected"]
    said = ms.check_text({"ok": True, "text": "да", "ttft_s": 0.2, "tok_s": 50.0, "seconds": 1.0, "cost_usd": 0})
    assert said == "«да» · первое слово через 0.2 с · 50.0 ток/с · всего 1.0 с · бесплатно"
    assert ms.check_text({"ok": False, "error": "нет ключа"}) == "Не ответила: нет ключа"


def test_the_models_sheet_in_the_window(qapp):
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.models)
        setup = app.model_setup
        sheet = quick.window.findChild(QObject, "modelsSheet")
        QMetaObject.invokeMethod(sheet, "openFor", Q_ARG("QVariant", "llama-swap/coder"))
        wait_until(lambda: sheet.property("opened") and setup.modelId == "llama-swap/coder" and not setup.loading)
        assert [f["id"] for f in setup.numberFields] == ["temperature", "top_p", "top_k", "min_p", "repeat_penalty",
                                                         "max_tokens"]  # fmt: skip
        setup.applyPreset("precise")
        assert (setup.draft["temperature"], setup.draft["top_k"], setup.draft["preset"], setup.dirty) == (
            "0.2", "20", "precise", True)  # fmt: skip
        setup.setField("temperature", "0,4")  # one's own number: no longer the preset
        setup.setField("system_prompt", "Отвечай кодом.")
        setup.setFlag("hidden", True)
        setup.save()
        wait_until(lambda: not app.bg.busy and not setup.dirty)
        saved = next(c for c in reversed(client.model_calls) if c[0] == "save")
        assert saved[0] == "save" and saved[2] == {"hidden": True, "params": {"temperature": 0.4, "top_p": 0.9,
                                                   "top_k": 20}, "system_prompt": "Отвечай кодом."}  # fmt: skip
        assert setup.version == 1 and "Сохранено" in app.flashText
        setup.setField("temperature", "9")
        setup.save()
        wait_until(lambda: not app.bg.busy and setup.error)
        assert "от 0 до 2" in setup.error and setup.dirty  # the hub's refusal, the draft kept
        setup.discard()
        setup.check()
        wait_until(lambda: not app.bg.busy and not setup.checking)
        assert setup.checkText.startswith("«на связи» · первое слово через 0.3 с")
        setup.open("anthropic/claude-opus-5-5")
        wait_until(lambda: not app.bg.busy and setup.modelId == "anthropic/claude-opus-5-5" and not setup.loading)
        assert setup.numberFields == [{"id": "max_tokens", "name": "Предел ответа", "hint": "токенов"}]
        setup.open("*")
        wait_until(lambda: not app.bg.busy and setup.general)
        assert client.model_calls[-1] == ("read", "")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_picker_leaves_hidden_models_out_and_a_model_sets_its_depth(app):
    hidden = [{**MODELS[0], "hidden": True}, {**MODELS[1], "effort": "low"}]
    items = rows.picker_items(hidden, set(), "llama-swap/coder")
    assert [i["id"] for i in items if i["id"]] == ["llama-swap/coder"]
    assert "anthropic/claude-opus-5-5" in [i["id"] for i in rows.picker_items(hidden, set(), MODELS[0]["id"])]
    app._set_models(hidden)
    app.chooseEffort("max")
    app.chooseModel("anthropic/claude-opus-5-5")
    app.chooseModel("llama-swap/coder")
    assert app._effort == "low"  # the model's own depth (D39)


def test_program_rows_filters_and_questions():
    from app.ui import programs

    shown = programs.program_rows(PROGRAMS, "all", "")
    assert [r["name"] for r in shown] == ["7-Zip", "Блокнот", "Telegram"]  # gone and undecided: not shown
    assert shown[0]["detail"] == "Igor Pavlov · 26.02" and shown[1]["detail"].endswith("Microsoft Store")
    assert shown[2]["fresh"] and shown[1]["path"].endswith("Notepad")
    assert [r["name"] for r in programs.program_rows(PROGRAMS, "undecided", "")] == ["Блокнот", "Telegram"]
    assert [r["name"] for r in programs.program_rows(PROGRAMS, "new", "")] == ["Telegram"]
    assert [r["name"] for r in programs.program_rows(PROGRAMS, "all", "pavlov")] == ["7-Zip"]  # by publisher too
    assert programs.counts(PROGRAMS) == {"all": 3, "allowed": 1, "denied": 0, "undecided": 2, "new": 1}
    assert [q["name"] for q in programs.questions(PROGRAMS)] == ["Telegram"]
    gone_but_allowed = {**PROGRAMS[3], "decision": "allow"}  # its decision is worth seeing
    assert programs.program_rows([gone_but_allowed], "all", "")[0]["detail"] == "не найдена на компьютере"
    # 25.2: a Steam game by its folder, a program seen by its open window
    game = {"id": "g", "name": "Witcher", "kind": "game", "exe": "", "folder": "D:\\Steam\\common\\Witcher",
            "decision": None, "fresh": True, "removed": False}  # fmt: skip
    seen = {"id": "w", "name": "Terminal (mintty)", "kind": "window", "publisher": "Thomas Wolff",
            "exe": "C:\\Git\\usr\\bin\\mintty.exe", "decision": None, "fresh": True, "removed": False}  # fmt: skip
    rows_ = programs.program_rows([game, seen], "all", "")
    assert [(r["detail"], r["path"]) for r in rows_] == [("игра Steam", "D:\\Steam\\common\\Witcher"),
                                                         ("Thomas Wolff · найдена по открытому окну",
                                                          "C:\\Git\\usr\\bin\\mintty.exe")]  # fmt: skip


def test_the_programs_window_part(app):
    progs, client = app.programs, app.client
    wait_until(lambda: progs.rows.rows)
    assert [r["name"] for r in progs.rows.rows] == ["7-Zip", "Блокнот", "Telegram"]
    assert [q["name"] for q in progs.pending] == ["Telegram"] and progs.offer == 0
    assert progs.filters[0]["name"] == "Все 3" and "Агенту разрешено программ: 1" in progs.status

    asked = []
    progs.pendingChanged.connect(lambda: asked.append(len(progs.pending)))
    progs.decide("ptg", "allow")  # from the question: the list and the question follow at once
    assert progs.pending == [] and asked == [0]
    assert next(r for r in progs.rows.rows if r["id"] == "ptg")["decision"] == "allow"
    wait_until(lambda: ("decide", "ptg", "allow") in client.program_calls)
    progs.decide("ptg", "")  # taking it back: not decided (no)
    wait_until(lambda: ("decide", "ptg", None) in client.program_calls)

    progs.setView("undecided")
    assert [r["name"] for r in progs.rows.rows] == ["Блокнот", "Telegram"]
    progs.setQuery("блок")
    assert [r["name"] for r in progs.rows.rows] == ["Блокнот"]
    progs.setQuery("")
    progs.setView("all")

    # a new program found by the hub: a notice outside the window, and the question
    client.program_list.append({**PROGRAMS[2], "id": "pnew", "name": "Новая", "first_seen": "2026-10-27T17:00:00Z"})
    notices = []
    progs.requested.connect(lambda title, text: notices.append((title, text)))
    send(app, "programs.found", {"programs": [{"id": "pnew", "name": "Новая"}]})
    wait_until(lambda: [q["id"] for q in progs.pending] == ["pnew"])
    assert notices == [("Новая программа", "Новая — разрешить автономному агенту?")]
    progs.later("pnew")
    assert progs.pending == []
    wait_until(lambda: ("later", "pnew") in client.program_calls)

    # after the first look: one offer, and "choose" opens the list on the undecided
    client.program_offer = {"count": 223}
    progs.load()
    wait_until(lambda: progs.offer == 223)
    opened_ui = []
    app.uiRequested.connect(opened_ui.append)
    progs.openList()
    assert progs.offer == 0 and opened_ui == ["programs"] and progs.view == "undecided"
    wait_until(lambda: ("dismiss",) in client.program_calls)

    progs.addProgram("file:///C:/Tools/portable.exe")
    wait_until(lambda: any(r["name"] == "Portable" for r in progs.rows.rows))
    assert ("add", "C:/Tools/portable.exe") in client.program_calls
    progs.removeProgram("pm")
    wait_until(lambda: all(r["id"] != "pm" for r in progs.rows.rows))
    progs.scan()
    assert progs.scanning
    wait_until(lambda: ("scan",) in client.program_calls)
    from app.ui import feed_rows

    found = feed_rows.describe({"type": "programs.found", "payload": {"programs": [{"name": "Telegram"}]}},
                               projects={}, chats={})  # fmt: skip
    assert found["title"] == "Найдена новая программа: «Telegram»" and found["group"] == "agent"
    decided = {"type": "program.decided", "payload": {"name": "Telegram", "decision": "deny"}}
    assert feed_rows.describe(decided, projects={}, chats={})["title"] == "Программа «Telegram»: запрещена агенту"


def test_the_program_question_and_list_in_the_window(qapp):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def drawn(condition) -> None:
        """Offscreen, frames drive a popup's fade in and out: draw them while waiting."""
        for _ in range(150):
            quick.window.grabWindow()
            QApplication.processEvents()
            if condition():
                return
            time.sleep(0.02)
        assert condition()

    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.programs.pending)
        for _ in range(10):
            QApplication.processEvents()
        popup = quick.window.findChild(QObject, "newProgramPopup")
        drawn(lambda: popup.property("opened"))
        assert quick.window.findChild(QObject, "newProgramName").property("text") == "Telegram"
        quick.window.findChild(QObject, "denyProgram").clicked.emit()
        wait_until(lambda: ("decide", "ptg", "deny") in client.program_calls)
        drawn(lambda: not popup.property("visible"))

        client.program_offer = {"count": 223}  # the first look's offer
        app.programs.load()
        drawn(lambda: popup.property("opened"))
        quick.window.findChild(QObject, "chooseProgramsOffer").clicked.emit()
        sheet = quick.window.findChild(QObject, "programsSheet")
        drawn(lambda: sheet.property("opened"))
        listed = quick.window.findChild(QObject, "programList")
        assert listed.property("count") == 1  # the undecided: Блокнот
        QMetaObject.invokeMethod(sheet, "close")
        assert quick.warnings == []
    finally:
        quick.close()


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
    # one text (24.1), its colours the editor's highlighter's: the lexer's name; the last newline is no empty line
    code = project_rows.code_view("".join(f"x = {n}\r\n" for n in range(10)) + "\tlong = 1\n", "a.py")
    assert code["text"] == "".join(f"x = {n}\n" for n in range(10)) + "\tlong = 1" and "chunks" not in code
    assert (code["lexer"], code["lines"], code["digits"]) == ("python", 11, 2)
    assert code["columns"] == project_rows.TAB_COLUMNS + len("long = 1")  # the width, without a layout
    plain = project_rows.code_view("<b>не html</b>", "notes.unknownext")
    assert plain["text"] == "<b>не html</b>" and plain["lexer"] == ""
    big = project_rows.code_view("x = 1\n" * 200_000, "big.py")  # 1.2 MB: in pieces, plain
    assert "text" not in big and big["lexer"] == "" and len(big["chunks"]) == 667  # of 300 lines
    diff = project_rows.diff_view("@@ -1 +1 @@\n-old <x>\n+new\n same\n")
    assert diff == {"text": "@@ -1 +1 @@\n-old <x>\n+new\n same", "lexer": "diff", "columns": 11, "digits": 0}
    view = project_rows.file_view({"path": "src/app.py", "size": 26, "text": FILES["src/app.py"], "encoding": "utf-8"})
    assert (view["name"], view["folder"], view["linesText"], view["sizeText"]) == ("app.py", "src", "2 строки", "26 Б")
    assert view["lang"] == "python" and view["lexer"] == "python"  # the header's language, the viewer's colours


def test_projects_tree_files_and_git(app, clipboard_stand_in):
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
    assert projects.file["lexer"] == "python" and projects.file["hasDiff"] and projects.file["mark"] == "M"
    assert projects.file["linesText"] == "2 строки"
    assert projects.openPath == "src/app.py"  # what the tree's rows read: not the whole file each
    assert "chunks" not in projects.file and "text" not in projects.file  # the text goes to the viewer alone
    assert projects.fileShown["key"] == "src/app.py" and projects.fileShown["lexer"] == "python"
    assert projects.fileShown["text"] == FILES["src/app.py"].rstrip("\n")
    projects.copyFile()
    assert clipboard_stand_in.text() == FILES["src/app.py"]
    projects.showDiff(True)
    wait_until(lambda: projects.file.get("diffMode"))
    assert "+print('new')" in projects.fileShown["text"] and projects.fileShown["lexer"] == "diff"
    assert projects.fileShown["key"] == "src/app.py:diff" and projects.fileShown["digits"] == 0
    projects.openFile("logo.png", False)
    wait_until(lambda: projects.file.get("path") == "logo.png")
    assert projects.file["binary"] and not projects.file["hasDiff"]
    projects.openFile("old.py", True)  # deleted: only its diff
    wait_until(lambda: projects.file.get("path") == "old.py")
    assert projects.file["missing"] and projects.file["diffMode"]
    shown = projects.fileShown
    app.setTheme("light")
    assert projects.fileShown == shown  # the colours are the viewer's highlighter's: the text is not sent again
    projects.closeFile()
    assert projects.file == {} and projects.openPath == "" and projects.fileShown == {}


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
    wait_until(lambda: "changed" in projects.fileShown["text"])
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
    titles = {kind: feed_rows.describe({"type": kind, "payload": payload}, projects={}, chats={})["title"]
              for kind, payload in [("secrets.changed", {"title": "Google Gemini", "set": True}),
                                    ("connections.denied", {"wait": 30}),
                                    ("llama_swap.started", {"ready": True})]}  # fmt: skip
    assert titles == {"secrets.changed": "Подключение «Google Gemini» задано",
                      "connections.denied": "Неверный пароль подключений",
                      "llama_swap.started": "Хаб запустил llama-swap"}  # fmt: skip


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
    # a window open for days does not keep every event: the oldest wait for "show more"
    from app.ui.feed import KEPT

    for n in range(KEPT):
        app.on_event({"id": 100 + n, "ts": "2026-10-27T02:00:00.000Z", "type": "port.closed", "project_id": "p1",
                      "payload": {"project_id": "p1", "port": n, "name": "python.exe"}})  # fmt: skip
    assert len(feed._events) == KEPT and feed.more and feed._events[0]["id"] == 100 + KEPT - 1


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
    batch = {"tasks": [{"task_id": "a", "agent_profile": "Писатель", "prompt": "x"},
                       {"task_id": "b", "agent_profile": "Тестер", "prompt": "y", "dependencies": ["a"]},
                       {"task_id": "c", "agent_profile": "Писатель", "prompt": "z"}]}  # fmt: skip
    assert rows.tool_label("delegate_tasks", batch) == "Поручаю 3 задачи: «Писатель», «Тестер»"  # 16.1, D50
    assert rows.tool_label("delegate_tasks", {}) == "Поручаю 0 задач"
    assert rows.tool_label("generate_image", {"prompt": "рыжая лиса в снегу"}) == "Рисую «рыжая лиса в снегу»"  # 29.6а
    assert rows.tool_label("generate_video", {"prompt": "лиса бежит"}) == "Снимаю видео «лиса бежит»"  # 29.8б
    film = {"id": "f1", "name": "generate_video", "input": {"prompt": "лиса"}, "state": "running"}
    (filmed,) = rows.with_results([film], [{"type": "tool_result", "id": "f1", "name": "generate_video",
                                            "output": "Filmed", "error": False, "blob": "ef" * 32,
                                            "media_type": "image/webp"}])  # fmt: skip
    assert (filmed["video"], filmed["picture"]) == ("ef" * 32, "")  # a video plays from its file, not a provider
    from app.ui.install_view import picture_rows

    # 29.8а: a video model where videos are only in the cloud
    clip = {"id": "wan", "name": "Wan", "verdict": "no", "words": "видео на этом компьютере — только в облаке",
            "installed": [], "size": 9_000_000_000}  # fmt: skip
    (shown,) = picture_rows([clip])
    assert (shown["tone"], shown["canInstall"], shown["words"]) == ("error", False, clip["words"])
    from app.ui import agents as agent_view

    assert agent_view.TOOL_LABELS["delegate_tasks"] == "давать задачи пачкой, параллельно (координатор)"
    assert agent_view.TOOL_LABELS["generate_image"] == "рисовать картинки"
    call = {"id": "g1", "name": "generate_image", "input": {"prompt": "лиса"}, "state": "running"}
    drawn = {"type": "tool_result", "id": "g1", "name": "generate_image", "output": "Drawn: 640×384", "error": False,
             "blob": "ab" * 32, "media_type": "image/png", "size": 100}  # fmt: skip
    (shown,) = rows.with_results([call], [drawn])
    assert shown["picture"] == "image://pictures/blob/" + "ab" * 32 and shown["state"] == "ok"
    (plain,) = rows.with_results([call], [{**drawn, "blob": "", "error": True}])
    assert plain["picture"] == ""
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

    app.newChatAbout("p1")  # a session of the project: it stays in «Проекты» (2026-10-28, the user's word)
    assert app.mode == "projects" and app.showProjectChat and app.currentId == ""
    assert [p["id"] for p in app.chatProjects] == ["p1"]
    app.setMode("chats")


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
    assert projects.card is projects.card  # read by some twenty bindings: built once, not once per read
    first = projects.card
    projects.cardChanged.emit()  # a theme switch, say: same inputs, same card
    assert projects.card is first
    app.chooseModel("llama-swap/coder")  # the writer is part of the card: a new one
    assert projects.card is not first and projects.card["writer"] == "Coder"
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
    lacks = ["Ключевые механизмы и где они"]  # 18.2: a card older than the section is worth an update too
    assert project_rows.card_changes({"commits": 2, "significant": False}, lacks) == (
        "В карточке нет раздела «Ключевые механизмы и где они»")  # fmt: skip
    assert project_rows.card_changes({"commits": 25, "significant": True}, lacks) == (
        "С подтверждения карточки: 25 коммитов; в карточке нет раздела «Ключевые механизмы и где они»")  # fmt: skip
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
    app.setMode("projects")
    app.projects.openProject("p1")
    wait_until(lambda: terms.tabs)
    assert [(t["title"], t["running"], t["note"]) for t in terms.tabs] == [("PowerShell", True, "работает")]
    terms.setViewSize(40, 5)
    terms.setPanelOpen(True)
    terms.openTerminal("t1")
    wait_until(lambda: terms._emus["t1"].loaded)
    assert terms.lines.rows[0]["html"] == "PS&nbsp;D:\\Code&gt;"
    assert terms.cursorVisible and (terms.cursorX, terms.cursorY) == (len(TAIL), 0)

    # an event that overlaps the tail: only its new part is played - and laid out only while the panel shows
    app.setMode("chats")
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(TAIL) - 3, "data": "e> dir\r\n"})
    assert terms.lines.rows[0]["html"] == "PS&nbsp;D:\\Code&gt;"
    app.setMode("projects")
    assert terms.lines.rows[0]["html"] == "PS&nbsp;D:\\Code&gt;&nbsp;dir"
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(TAIL) + 5,
                                  "data": "".join(f"\x1b[32mline {n}\x1b[0m\r\n" for n in range(8))})  # fmt: skip
    keys = [r["key"] for r in terms.lines.rows]
    assert len(keys) == 5 + 5 and keys[:5] == [f"h{n}" for n in range(5)]  # 5 lines scrolled into history
    assert any("color:#0dbc79" in html for html in shown_lines(terms))  # green, from the dark palette

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


def test_terminal_output_the_window_missed_is_read_by_offset(app, monkeypatch):
    """Events lost in a break (the event queue overflowed, the link dropped) show as a gap in the offsets: the window
    reads the gap from the hub. A terminal off screen keeps its output raw and plays it when it shows (D46)."""
    from app.ui import terminals

    terms, client = app.terminals, app.client
    app.setMode("projects")
    app.projects.openProject("p1")
    wait_until(lambda: terms.tabs)
    terms.setViewSize(40, 5)
    terms.setPanelOpen(True)
    terms.openTerminal("t1")
    emu = terms._emus["t1"]
    wait_until(lambda: emu.loaded and not emu.loading)

    def screen():
        return emu.screen.plain_text()

    # "one" never came as an event: the next event starts past it, and the window asks for what it missed
    client.term_text = TAIL + "one\r\ntwo\r\n"
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(TAIL) + 5, "data": "two\r\n"})
    wait_until(lambda: not emu.loading)
    assert client.output_asked[-1] == len(TAIL) and emu.end == len(client.term_text)
    assert "one" in screen() and "two" in screen() and "one" in "".join(shown_lines(terms))

    # the hub no longer has all of the gap: the terminal is played again from what it has
    client.term_kept = 8
    client.term_text += "x" * 40 + "\r\nlast\r\n"
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(client.term_text) - 6, "data": "last\r\n"})
    wait_until(lambda: not emu.loading)
    assert "last" in screen() and "two" not in screen() and emu.end == len(client.term_text)

    # off screen: kept raw, not parsed; played when it shows
    app.setMode("chats")
    client.term_text += "hidden\r\n"
    send(app, "terminal.output", {"terminal_id": "t1", "offset": emu.end, "data": "hidden\r\n"})
    assert emu.waiting and "hidden" not in screen()
    app.setMode("projects")
    assert not emu.waiting and "hidden" in screen()

    # too much off screen: let go, and read from the hub when it shows
    monkeypatch.setattr(terminals, "WAITING_MAX", 20)
    app.setMode("chats")
    before = emu.end
    client.term_kept = 1_000_000
    for n in range(3):
        piece = f"flood {n} .....\r\n"
        send(app, "terminal.output", {"terminal_id": "t1", "offset": len(client.term_text), "data": piece})
        client.term_text += piece
    assert not emu.waiting and emu.missed
    app.setMode("projects")
    wait_until(lambda: not emu.loading)
    assert client.output_asked[-1] == before and "flood 2" in screen() and not emu.missed

    # back after a break in the link: what the terminal printed meanwhile is read at once
    client.term_text += "after the break\r\n"
    app._link = False
    app._on_connected(True)
    wait_until(lambda: "after the break" in screen())


def test_without_a_hub_nothing_stays_going_on(app):
    """A call the window cannot make (no hub connected) undoes the "searching…" / "saving…" it set up front: it
    used to stay forever, since only the call's answer cleared it."""
    projects, terms = app.projects, app.terminals
    app.setMode("projects")
    projects.openProject("p1")
    wait_until(lambda: projects.currentId == "p1" and terms.tabs and not app.bg.busy)
    client, app.client = app.client, None
    try:
        projects.scanLinks()
        assert not projects.links["scanning"]
        projects.search("print", False, False)
        projects._run_search()  # the typing pause is over
        assert not projects._searching and projects.searchSummary != "поиск…"
        projects.draftCard()
        assert projects.card.get("state") != "drafting" and "не подключён" in projects.card["error"]
        app.connections._run(lambda: None)
        assert not app.connections._busy and "не подключён" in app.connections._error
        terms.openTerminal("t1")
        assert not terms._emus["t1"].loading  # not waiting for an answer that never comes
    finally:
        app.client = client
    terms.openTerminal("t1")  # the hub is back: the next opening loads the terminal
    wait_until(lambda: terms._emus["t1"].loaded)


def shown_lines(terms, rows=None) -> list[str]:
    """The terminal's lines (all, or those `rows`) as the view reads them: a scrollback line becomes rich text when
    it is asked for."""
    from app.ui.terminals import LINE_ROLES

    role = Qt.ItemDataRole.UserRole + LINE_ROLES.index("html")
    rows = range(terms.lines.rowCount()) if rows is None else rows
    return [terms.lines.data(terms.lines.index(i, 0), role) for i in rows]


def test_a_flood_of_output_is_made_into_rich_text_only_where_it_shows(app, monkeypatch):
    """Every line that went up into the scrollback was made into rich text at once: 200 000 lines of output, most
    let go unseen, kept the window's thread busy for seconds (D46, step 13.7). A line becomes rich text when the
    view first asks for it - and a theme switch no longer loses the scrollback's colours."""
    from app.ui import term_screen

    rendered = []
    line_html = term_screen.line_html
    monkeypatch.setattr(term_screen, "line_html", lambda *args: (rendered.append(1), line_html(*args))[1])
    terms = app.terminals
    app.setMode("projects")
    app.projects.openProject("p1")
    wait_until(lambda: terms.tabs)
    terms.setViewSize(40, 5)
    terms.setPanelOpen(True)
    terms.openTerminal("t1")
    wait_until(lambda: terms._emus["t1"].loaded)
    rendered.clear()
    flood = "".join(f"\x1b[32mline {n}\x1b[0m\r\n" for n in range(5000))
    send(app, "terminal.output", {"terminal_id": "t1", "offset": len(TAIL), "data": flood})
    assert len(rendered) <= 10  # the screen's own lines, not the 5000 that scrolled up
    assert len(terms.lines.rows) == term_screen.HISTORY_LINES + 5
    (first,) = shown_lines(terms, [0])  # what the view asks for is made then
    assert "line&nbsp;1996" in first and "color:#0dbc79" in first and len(rendered) <= 11
    app.setTheme("light")
    assert "color:#00bc00" in shown_lines(terms, [0])[0]  # the light palette's green: the colours stayed
    assert "line 4999" in terms._emus["t1"].screen.plain_text()


def test_the_emulators_quick_drawing_draws_what_pyte_draws():
    """pyte made a new Char for every character drawn: 70% of the window's time in a flood of output (D46, step
    13.7). TermScreen draws ordinary text faster - and must leave the screen, the cursor, the scrollback and what is
    marked changed exactly as pyte's own drawing does, whatever comes."""
    import random

    import pyte

    from app.ui.term_screen import TermScreen

    class Reference(TermScreen):
        draw = pyte.Screen.draw

    pieces = ["abc", "строка ", "x" * 37, "日本", "é", " ", "\r\n", "\n", "\r", "\x1b[31m", "\x1b[1;42m",
              "\x1b[0m", "\x1b[4h", "\x1b[4l", "\x1b[?7l", "\x1b[?7h", "\x1b[5;3H", "\x1b[2K", "\x1b(0", "\x1b(B",
              "lqqk", "\x0e", "\x0f", "\x1b[2;4r", "\x1b[r", "\t",
              # two cells wide (24.4): at a line's last column, an odd number of them, among narrow ones; a
              # combining mark and one of no width (pyte's still)
              "日本語のテキスト", "x" * 11 + "語", "a日b本c", "\U0001f600", "表" * 13, "e\u0301", "\u200b",
              "a\u0300b"]  # fmt: skip
    rng = random.Random(13)
    for case in range(600):
        quick, reference = TermScreen(12, 4), Reference(12, 4)
        streams = pyte.Stream(quick), pyte.Stream(reference)
        for _ in range(rng.randint(1, 60)):
            piece = rng.choice(pieces)
            for stream in streams:
                stream.feed(piece)

        def state(screen):
            cells = [[screen.buffer[y][x] for x in range(screen.columns)] for y in range(screen.lines)]
            return cells, screen.cursor.x, screen.cursor.y, screen.dirty, [s.text for s in screen.scrolled]

        assert state(quick) == state(reference), case

    # characters two cells wide are the quick drawing's too (24.4: pyte took them 2-3 times slower); combining marks
    # and characters of no width are pyte's
    asked = []
    real = pyte.Screen.draw
    pyte.Screen.draw = lambda screen, data: asked.append(data) or real(screen, data)
    try:
        screen = TermScreen(20, 3)
        stream = pyte.Stream(screen)
        stream.feed("日本語 и строка\U0001f600")
        assert asked == [] and screen.buffer[0][0].data == "日" and screen.buffer[0][1].data == ""
        stream.feed("e\u0301")
        assert asked == ["e\u0301"]
    finally:
        pyte.Screen.draw = real


def test_terminal_lines_follow_output_change_by_change(app, monkeypatch):
    """Output into a full scrollback lets its oldest lines go on every event: the line model loses those and gains
    the new ones, not all its rows again - and always holds what a full layout would."""
    import random

    from app.ui import term_screen

    monkeypatch.setattr(term_screen, "HISTORY_LINES", 40)
    terms = app.terminals
    terms._open, terms._current = True, "tx"
    app.setMode("projects")
    terms._cols, terms._rows = 30, 6
    emu = terms._emus["tx"] = terms._new_emu()
    emu.loaded = True
    terms._fit(emu)
    terms._show(emu)
    inserted: list[int] = []
    insert = terms.lines.insert
    monkeypatch.setattr(terms.lines, "insert", lambda at, rows: (inserted.append(len(rows)), insert(at, rows)))
    rng = random.Random(7)
    for n in range(200):
        lines = "".join(f"\x1b[3{k % 8}mline {n}.{k}\x1b[0m\r\n" for k in range(rng.randint(0, 6)))
        emu.stream.feed(lines + ("prompt> " if n % 3 else ""))
        terms._update(emu)
        screen = emu.screen
        full = [{"key": f"h{screen.dropped + i}", "html": text} for i, text in enumerate(screen.scrolled)]
        full += [{"key": f"s{y}", "html": screen.html(y)} for y in range(screen.lines)]
        assert terms.lines.rows == full, n
    assert emu.screen.dropped > 300 and max(inserted) <= 6  # only the new lines went in, never the whole list


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
    from PySide6.QtCore import QMetaObject, QObject, QPointF

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
        text = code.findChild(QObject, "codeText")  # the file as one read-only text (24.1)
        flick = code.findChild(QObject, "codeFlick")
        assert code.property("visible") and text.property("readOnly") and text.property("lineCount") == 2
        # a long Cyrillic or tabbed line fits
        app.client.files["wide.py"] = "x = '" + "широкая строка " * 30 + "'\n\t\tif x:\n"
        app.projects.openFile("wide.py", False)
        wait_until(lambda: app.projects.file.get("path") == "wide.py")
        for _ in range(10):
            QApplication.processEvents()
        assert text.property("width") >= text.property("implicitWidth") > 2000
        # a long file: selected from its first line to its last; a line far down shown and marked; the screen
        # coloured by the viewer's highlighter, the top of the file not yet
        app.client.files["long.py"] = "".join(f"line_{i} = {i}\n" for i in range(12_000))
        app.projects.openFileAt("long.py", 9005)
        wait_until(lambda: app.projects.file.get("path") == "long.py")
        for _ in range(20):
            QApplication.processEvents()
        assert text.property("lineCount") == 12_000
        QMetaObject.invokeMethod(text, "selectAll")
        assert text.property("selectedText") == app.client.files["long.py"].rstrip("\n")
        QMetaObject.invokeMethod(text, "deselect")
        mark = code.findChild(QObject, "gotoMark")  # the viewer's (the compare sheet has one too)
        line_height = text.property("contentHeight") / 12_000
        assert mark.property("visible") and abs(mark.property("height") - line_height) < 0.5
        assert abs(mark.property("y") - text.property("topPadding") - 9004 * line_height) < 1
        assert flick.property("contentY") < mark.property("y") < flick.property("contentY") + flick.property("height")
        document = app.readers._colours["file"]._document
        wait_until(lambda: document.findBlockByNumber(9004).userState() >= 0)
        assert document.findBlockByNumber(9004).layout().formats() and document.firstBlock().userState() == -1
        # from there to a diff: its text drawn (put in in the same go as the jump to the top, it was not)
        app.projects.openFile("src/app.py", True)
        wait_until(lambda: app.projects.file.get("diffMode") and text.property("lineCount") == 5)
        for _ in range(20):
            QApplication.processEvents()
        shot, corner = quick.window.grabWindow(), flick.mapToScene(QPointF(0, 0))
        left, top = int(corner.x()), int(corner.y())
        background = shot.pixelColor(left + 600, top + 600).lightness()
        inked = [(x, y) for y in range(top, top + 90) for x in range(left, left + 100)
                 if abs(shot.pixelColor(x, y).lightness() - background) > 60]  # fmt: skip
        assert len(inked) > 100
        # far down a long file that shrinks on disk: the short text at once (while a text is replaced, its height and
        # its line count change one after the other: 100 000 line numbers for a moment held the window 7 s)
        app.projects.openFileAt("long.py", 9005)
        wait_until(lambda: flick.property("contentY") > 100_000)
        began = time.monotonic()
        app.client.files["long.py"] = "short = 1\n"
        send(app, "project.files", {"project_id": "p1", "changed": ["long.py"], "folders": [""], "more": False})
        wait_until(lambda: text.property("text") == "short = 1")
        assert time.monotonic() - began < 2 and code.findChild(QObject, "lineNumbers").property("count") < 100
        # over a megabyte: in pieces, plain - only those near the screen laid out (2026-10-28)
        app.client.files["huge.txt"] = "".join(f"line_{i} = {i}\n" for i in range(80_000))
        app.projects.openFile("huge.txt", False)
        wait_until(lambda: app.projects.file.get("path") == "huge.txt")
        for _ in range(20):
            QApplication.processEvents()
        pieces = code.findChild(QObject, "codePieces")
        assert pieces.property("visible") and pieces.property("count") == 80_000 // project_rows.CHUNK_LINES + 1
        assert not flick.property("visible") and text.property("text") == ""  # the long text let go
        app.projects.openFile("src/app.py", False)
        wait_until(lambda: app.projects.file.get("path") == "src/app.py")
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
        mark = code.findChild(QObject, "gotoMark")
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
        # closed, it has no rows to rebuild on every event the hub stores (offscreen, frames drive its slide-out)
        for _ in range(100):
            quick.window.grabWindow()
            QApplication.processEvents()
            if not sheet.property("visible"):
                break
            time.sleep(0.02)
        assert not sheet.property("visible")
        assert len(quick.window.findChild(QObject, "feedItems").childItems()) == 1  # the Repeater alone
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


# --- stage 4: approvals and Claude Code ---


def test_approval_rows():
    from app.ui.approvals import approval_rows

    projects = {"p1": {"id": "p1", "name": "LocalChat", "path": "D:\\Code\\LocalChat"}}
    preview = {"kind": "command", "text": "git push", "note": "Push"}
    command = {**APPROVAL, "id": "ap2", "tool": "Bash", "kind": "command", "destructive": True,
               "input": {"command": "git push"}, "preview": preview}  # fmt: skip
    edit, push = approval_rows([APPROVAL, command], projects=projects, chats={"c1": "Первый"})
    assert edit["title"] == "Править hub/chat.py" and edit["who"] == "Claude Code"
    assert edit["where"] == "LocalChat · «Первый»" and edit["note"] == "hub/chat.py"
    assert [line["mark"] for line in edit["lines"]] == ["file", "file", "hunk", "", "del", "add"]
    assert edit["chatScope"] == "все Edit в чате" and edit["time"].count(":") == 1
    assert push["title"] == "Выполнить команду" and push["destructive"] and push["lines"] == [{"text": "git push",
                                                                                            "mark": ""}]  # fmt: skip
    assert push["chatScope"] == "эта команда в этом чате" and push["icon"] == "terminal"
    # the hub's own models ask with no client: the chat's model asks, with the hub's tool names said in words
    own = {**APPROVAL, "id": "ap5", "client": None, "tool": "edit_file", "input": {"project": "p1", "path": "a.txt"}}
    search = {**own, "id": "ap6", "conversation_id": "c9", "tool": "web_search", "input": {"query": "qt 6.12"}}
    mine, found = approval_rows([own, search], projects=projects, chats={}, speakers={"c1": "Coder"})
    assert (mine["who"], mine["title"]) == ("Coder", "Править a.txt")
    assert (found["who"], found["title"]) == ("Агент", "Искать в сети «qt 6.12»")  # an unknown chat's model
    back = {**own, "id": "ap8", "tool": "goal_rollback", "input": {"checkpoint": "этап 1"}}
    assert approval_rows([back], projects=projects, chats={})[0]["title"] == "Вернуть файлы к точке «этап 1»"
    assert edit["hunks"] == [] and edit["allowText"] == "Разрешить"  # one hunk: nothing to choose
    # a write of several hunks (17.3, D51): each with its tick, its lines marked; unticked - the button says so
    parted = {**APPROVAL, "id": "ap7", "preview": PARTED}
    (whole,) = approval_rows([parted], projects=projects, chats={})
    assert [(h["number"], h["ticked"]) for h in whole["hunks"]] == [(1, True), (2, True), (3, True)]
    assert whole["hunks"][1]["header"] == "@@ -17,4 +17,4 @@" and whole["allowText"] == "Разрешить"
    assert [line["mark"] for line in whole["hunks"][1]["lines"]] == ["", "del", "add", ""]
    (part,) = approval_rows([parted], projects=projects, chats={}, skipped={"ap7": {1}})
    assert (part["ticked"], part["hunkCount"]) == (2, 3) and not part["hunks"][1]["ticked"]
    assert part["allowText"] == "Разрешить отмеченное (2 из 3)"


def test_claude_code_tool_rows_and_footer():
    from app import presenter

    path = "D:/AI model/LocalChat/hub/claude_code.py"
    assert rows.tool_label("Read", {"file_path": path}) == "Читаю LocalChat/hub/claude_code.py"
    assert rows.tool_label("Edit", {"file_path": "hub/x.py"}) == "Правлю hub/x.py"
    assert rows.tool_label("Bash", {"command": "pytest  -q"}) == "Выполняю pytest -q"
    assert rows.tool_label("mcp__localchat__read_file", {"project": "p", "path": "a.py"}) == "Читаю a.py"
    assert rows.tool_label("mcp__localchat__get_ecosystem_map", {}) == "Смотрю карту проектов"
    assert rows.tool_label("Task", {"description": "найти тесты"}) == "Поручаю подагенту: найти тесты"
    built = rows.tools_of({"content": [{"type": "tool_call", "id": "t", "name": "mcp__localchat__git_log",
                                        "input": {"project": "p"}}]})  # fmt: skip
    assert built[0]["icon"] == "commit" and built[0]["project"] == "p"
    footer = presenter.answer_meta({"output_tokens": 30, "estimated": 1},
                                   stats={"subscription": True, "cost_estimate_usd": 0.0123}, cost=0.0)  # fmt: skip
    assert footer == "~30 ток. · подписка, по оценке ≈" + presenter.money(0.0123)
    assert presenter.claude_code_limit({"status": "rejected"}) == "Claude Code: лимит подписки исчерпан"
    assert "близко до " in presenter.claude_code_limit({"status": "allowed_warning", "resetsAt": 1890000000})


def test_claude_codes_subscription_in_the_status_bar_and_the_picker():
    """20.3: the window nearest its limit in the status bar (a reached one first), every window in its tooltip, and
    the same line at Claude Code's models in the picker."""
    from app import presenter

    resets = datetime.now().replace(hour=15, minute=45, second=0, microsecond=0).timestamp()
    week = {"status": "allowed", "window": "seven_day", "used": 0.4, "resets_at": None}
    five = {"status": "allowed_warning", "window": "five_hour", "used": 0.85, "resets_at": resets}
    assert presenter.subscription([]) == ("", "", False)
    line, tip, warn = presenter.subscription([five, week])
    assert line == "5 часов — 85 %, сброс в 15:45" and warn
    assert tip.splitlines()[1:] == ["5 часов — 85 %, сброс в 15:45", "неделя — 40 %"]
    reached = presenter.subscription([week, {"status": "rejected", "window": "seven_day_opus", "used": None,
                                             "resets_at": None}])  # fmt: skip
    assert reached[0] == "неделя Opus — исчерпан" and reached[2]
    assert presenter.subscription([week]) == ("неделя — 40 %", tip.splitlines()[0] + "\nнеделя — 40 %", False)
    claude = {"id": "claude-code/default", "name": "Claude Code", "locality": "cloud", "model_ref": "default",
              "capabilities": {"subscription": True}}  # fmt: skip
    (_section, item) = rows.picker_items([claude], set(), None, subscription="неделя — 40 %")
    assert item["note"] == "подписка Claude Code · работает в папке проекта чата · неделя — 40 %"


def test_claude_code_in_the_exams_table_spends_the_subscription():
    """20.5: Claude Code's runs cost no money - the exam's table says «подписка» where the others say what they cost."""
    from app.ui.proving_view import spent

    assert spent({"model_id": "claude-code/default", "cost_usd": 0.0}) == "подписка"
    assert spent({"model_id": "agent/m", "cost_usd": 0.0}) == "бесплатно"


def test_a_subagents_steps_under_its_call():
    """21.3: while Claude Code's subagent works, each step it takes is the last line of the call that started it and
    joins what the call shows opened; done, the call shows the subagent's steps, then its answer."""
    call = {"type": "tool_call", "id": "a1", "name": "Agent", "input": {"description": "найти тесты"}}
    shown = [{"id": "m1", "tools": rows.tools_of({"content": [call]})}]
    step = {"type": "call", "name": "Read", "input": {"file_path": "tests/test_x.py"}}
    (changed,) = rows.with_subagent_step(shown, "a1", step).values()
    assert changed[0]["summary"] == "→ Читаю tests/test_x.py" and changed[0]["output"] == "→ Читаю tests/test_x.py"
    assert rows.with_subagent_step(shown, "other", step) == {}  # no call of that id: nothing
    assert rows.with_subagent_step(shown, "a1", {"type": "result", "error": False}) == {}  # a quiet result: no line
    sub = [{"type": "text", "text": "Ищу тесты."}, step, {"type": "result", "name": "Read", "error": True,
                                                          "output": "нет файла"}]  # fmt: skip
    result = {"type": "tool_result", "id": "a1", "name": "Agent", "output": "Тестов нет", "error": False, "sub": sub}
    (done,) = rows.with_results(shown[0]["tools"], [result])
    assert done["summary"] == "Тестов нет"
    assert done["output"] == "Ищу тесты.\n→ Читаю tests/test_x.py\n  ошибка: нет файла\n\nТестов нет"


def test_how_long_a_request_waits_and_who_waits():
    from app.ui.approvals import age, waiting_rows

    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert [age(t, now) for t in ("2026-10-27T11:59:40Z", "2026-10-27T11:55:00Z", "2026-10-27T09:50:00.000Z",
                                  "2026-10-27T10:00:00Z", "2026-10-24T11:00:00Z", "?")] == [
        "только что", "5 мин", "2 ч 10 мин", "2 ч", "3 дн", ""]  # fmt: skip
    push = {**APPROVAL, "id": "ap2", "conversation_id": "run1", "client": "agent", "tool": "run_command",
            "kind": "command", "destructive": True, "created_at": "2026-10-27T11:00:00Z"}  # fmt: skip
    loose = {**APPROVAL, "id": "ap3", "conversation_id": None}
    projects = {"p1": {"id": "p1", "name": "LocalChat", "path": "D:\\Code\\LocalChat"}}
    rows_ = waiting_rows([APPROVAL, push, loose, {**APPROVAL, "id": "ap4"}], projects=projects,
                         chats={"c1": "Первый", "run1": ""}, current="run1", now=now)  # fmt: skip
    assert [(r["conversationId"], r["chat"], r["count"], r["more"], r["here"]) for r in rows_] == [
        ("c1", "Первый", 2, "ещё 1", False),
        ("run1", "Чат без названия", 1, "", True),
    ]  # no chat: not a row
    assert rows_[0]["what"] == "Claude Code: Править hub/chat.py" and not rows_[0]["destructive"]
    assert rows_[1]["what"] == "agent: Выполнить команду" and rows_[1]["destructive"] and rows_[1]["icon"] == "terminal"
    assert rows_[1]["age"] == "1 ч"


def test_the_approval_queue(app):
    approvals = app.approvals
    wait_until(lambda: approvals.count == 1)  # loaded with the rest when the window attached
    assert approvals.items[0]["title"] == "Править hub/chat.py"
    assert approvals.card == {} and approvals.cardCount == 0  # no chat open: no card
    cards = []
    approvals.hereChanged.connect(lambda: cards.append((approvals.card.get("id"), approvals.cardCount)))
    opened(app)  # chat c1 is open: its request is the card on top of it
    assert cards == [("ap1", 1)] and approvals.card["where"] == "LocalChat · «Первый»"
    request = {**APPROVAL, "id": "ap3", "tool": "Bash", "kind": "command", "input": {"command": "pytest"},
               "preview": {"kind": "command", "text": "pytest", "note": ""}}  # fmt: skip
    app.flash("")
    send(app, "approval.requested", {"approval": request})
    assert approvals.count == 2 and cards[-1] == ("ap1", 2)  # the oldest stays on top: "1 из 2"
    assert app.flashText == ""  # the open chat's card says it itself
    send(app, "approval.requested", {"approval": {**request, "id": "ap4", "conversation_id": "other"}})
    assert approvals.count == 3 and len(cards) == 2  # another chat's: only the shield, the flash and the notice
    assert "щита" in app.flashText
    assert [(w["conversationId"], w["count"], w["here"]) for w in approvals.waiting] == [
        ("c1", 2, True),
        ("other", 1, False),
    ]
    assert approvals.waiting[0]["more"] == "ещё 1" and approvals.waiting[1]["chat"] == "Чат без названия"
    send(app, "approval.decided", {"approval": {**request, "id": "ap4", "status": "expired"}})
    assert approvals.count == 2
    approvals.answer("ap1", False, "once", "сначала линтер")
    assert cards[-1] == ("ap3", 1)  # answered: the next one of this chat, at once
    wait_until(lambda: not app.bg.busy)
    assert app.client.answers == [("ap1", False, "once", "сначала линтер")] and approvals.count == 1
    approvals.answer("ap3", True, "chat", "")
    wait_until(lambda: not app.bg.busy)
    assert app.client.answers[-1] == ("ap3", True, "chat", "") and approvals.count == 0 and cards[-1] == (None, 0)
    approvals._pending = [{**APPROVAL, "id": "gone"}]
    approvals.answer("gone", True, "once", "")
    wait_until(lambda: not app.bg.busy)
    assert "Ответ не принят" in app.flashText and approvals.count == 0  # reloaded from the hub
    send(app, "claude_code.limit", {"info": {"status": "rejected"}})
    assert app.flashText == "Claude Code: лимит подписки исчерпан" and app.flashError


def test_claude_code_in_a_terminal(app):
    assert app.claudeCodeInstalled is True
    app.terminals.set_project("p1")
    app.terminals.newClaudeCode()
    wait_until(lambda: not app.bg.busy)
    assert app.client.claude_terminals == ["p1"] and app.terminals.panelOpen


def test_exporting_the_card_asks_first(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.currentId == "p1")
    projects.loadExport()
    wait_until(lambda: len(projects.exportTargets) == 3)
    claude, agents, cline = projects.exportTargets
    assert (claude["name"], claude["note"], claude["enabled"]) == ("CLAUDE.md", "создать файл", True)
    assert agents["note"] == "добавить блок в конец файла"
    assert (cline["name"], cline["enabled"]) == (".clinerules/localchat.md", False)  # already as the card is
    projects.exportCard("cline")
    assert app.question == {}  # nothing to write, nothing asked
    projects.exportCard("agents")
    assert app.question["title"] == "Записать карточку в AGENTS.md?"
    assert "остальное в файле не изменится" in app.question["text"] and "git" in app.question["text"]
    app.answer(False)
    wait_until(lambda: not app.bg.busy)
    assert not any(e[0] == "export" for e in client.log)  # declined: nothing written (D23)
    projects.exportCard("claude")
    app.answer(True)
    wait_until(lambda: any(e[0] == "export" for e in client.log))
    wait_until(lambda: not app.bg.busy)
    assert ("export", "p1", ["claude"]) in client.log and app.flashText == "Карточка записана в CLAUDE.md"


# --- stage 5: models write files ---


def test_write_rows_rules_and_feed():
    from app.ui import feed_rows, project_rows

    (row,) = project_rows.write_rows([CHANGE])
    assert row["path"] == "src/app.py" and row["note"].startswith("правка · чат «Первый» · ") and not row["reverted"]
    created = project_rows.write_rows([{**CHANGE, "before_hash": None, "chat": None, "reverted_at": "x"}])[0]
    assert created["note"].startswith("создан · ") and created["reverted"]
    mine = project_rows.write_rows([{**CHANGE, "tool": "save", "chat": None, "conversation_id": None}])[0]
    assert mine["note"].startswith("сохранено · вы · ")  # the user's own save in the editor (D47, 14.1)
    assert project_rows.rule_text("edit_file") == "править файлы"
    assert project_rows.rule_text("Bash:pytest -q") == "команда «pytest -q»"
    assert rows.tool_label("edit_file", {"project": "p", "path": "a.py"}) == "Правлю a.py"
    assert rows.tool_label("write_file", {"project": "p", "path": "b.md"}) == "Пишу b.md"
    shown = feed_rows.describe({"type": "file.changed", "payload": {"change": CHANGE}}, projects={}, chats={})
    assert shown["title"] == "Модель изменила src/app.py" and shown["detail"] == "чат «Первый»"
    assert feed_rows.describe({"type": "file.reverted", "payload": {"change": CHANGE}}, projects={},
                              chats={})["title"] == "Откачена правка src/app.py"  # fmt: skip


def test_model_writes_in_the_project_view(app):
    projects, client = app.projects, app.client
    projects.openProject("p1")
    wait_until(lambda: projects.fileChanges and projects.rules)
    assert projects.fileChanges[0]["path"] == "src/app.py"
    assert projects.rules == [{"id": 7, "text": "править файлы"}]
    projects.revertChange(3)
    assert app.question["title"] == "Откатить правку src/app.py?"
    app.answer(True)
    wait_until(lambda: app.question.get("title", "").startswith("src/app.py изменился"))  # changed since: asked again
    app.answer(True)
    wait_until(lambda: ("revert", 3, True) in client.log)
    wait_until(lambda: projects.fileChanges[0]["reverted"])
    assert app.flashText == "Правка src/app.py откачена"
    send(app, "file.changed", {"project_id": "p1", "change": CHANGE})  # the feed and the list follow the hub
    projects.removeRule(7)
    wait_until(lambda: projects.rules == [])


def test_the_chats_rights_mode(app):
    opened(app)
    assert app.contextSettings["mode"] == "ask"
    app.setChatMode("trusted")
    wait_until(lambda: not app.bg.busy)
    assert client_log_has(app, ("context", "c1", {"mode": "trusted"}))
    assert app.contextSettings["mode"] == "trusted"
    app.setChatMode("root")  # not a mode: ignored
    assert app.contextSettings["mode"] == "trusted"


def client_log_has(app, entry):
    return entry in app.client.log


def test_an_answer_for_the_project_in_the_queue(app):
    from app.ui.approvals import approval_rows

    projects = {"p1": {"id": "p1", "name": "LocalChat", "path": "D:/Code/LocalChat"}}
    (row,) = approval_rows([APPROVAL], projects=projects, chats={})
    assert row["projectScope"] == "всегда в «LocalChat»"
    push = {**APPROVAL, "destructive": True}
    assert approval_rows([push], projects=projects, chats={})[0]["projectScope"] == ""
    assert approval_rows([{**APPROVAL, "project_id": None}], projects=projects, chats={})[0]["projectScope"] == ""
    wait_until(lambda: app.approvals.count == 1)
    app.approvals.answer("ap1", True, "project", "")
    wait_until(lambda: not app.bg.busy)
    assert app.client.answers[-1] == ("ap1", True, "project", "")


def test_commands_in_the_window():
    from app.ui.approvals import approval_rows

    assert rows.tool_label("run_command", {"project": "p", "command": "pytest  -q"}) == "Выполняю pytest -q"
    projects = {"p1": {"id": "p1", "name": "LocalChat", "path": "D:/Code/LocalChat"}}
    preview = {"kind": "command", "text": "pip install x"}
    install = {**APPROVAL, "tool": "run_command", "kind": "command", "network": True,
               "input": {"command": "pip install x"}, "preview": preview}  # fmt: skip
    (row,) = approval_rows([install], projects=projects, chats={})
    assert row["title"] == "Выполнить команду" and row["alwaysAsk"] == "network" and row["projectScope"] == ""
    plain = approval_rows([{**install, "network": False}], projects=projects, chats={})[0]
    assert plain["alwaysAsk"] == "" and plain["projectScope"] == "всегда в «LocalChat»"
    # a command whose line does not name what it runs (D46, 13.11): its own reason, a destructive one's buttons
    hidden = {**install, "network": False, "destructive": True, "opaque": True, "input": {"command": "& $t push"}}
    row = approval_rows([hidden], projects=projects, chats={})[0]
    assert row["alwaysAsk"] == "opaque" and row["destructive"] and row["projectScope"] == ""
    # a command into the hub's own data (D46, 13.12): its own reason, allowed once, never for the chat or project
    token = {**install, "network": False, "hub_data": True, "input": {"command": "type data\\tokens\\app.token"}}
    row = approval_rows([token], projects=projects, chats={})[0]
    assert row["alwaysAsk"] == "hub" and not row["destructive"] and row["projectScope"] == ""


# --- stage 5.3: the hub's own agents ---


def test_agent_and_run_rows():
    from app.ui import agents, feed_rows

    (row,) = agents.profile_rows([AGENT], {"llama-swap/coder": "Coder"}, "a1")
    assert row["note"] == "Coder · только чтение · проектов: 1 · версия 3" and row["selected"]
    (run,) = agents.run_rows([RUN])
    assert (run["task"], run["status"], run["icon"], run["level"], run["active"]) == (
        "Проверь hub/chat.py", "ждёт подтверждения", "shield", "warn", True)  # fmt: skip
    failed = agents.run_rows([{**RUN, "status": "error", "shown_status": "error", "detail": "the step limit (2)"}])[0]
    assert failed["status"] == "ошибка: the step limit (2)" and failed["level"] == "error" and not failed["active"]
    started = feed_rows.describe({"type": "run.started", "payload": {"run": RUN, "resumed": True}}, projects={},
                                 chats={})  # fmt: skip
    assert started["title"] == "Агент «Ревьюер» взял задачу (продолжает после перезапуска)"
    finished = feed_rows.describe({"type": "run.finished", "payload": {"run": {**RUN, "status": "done"}}},
                                  projects={}, chats={})  # fmt: skip
    assert finished["title"] == "Агент «Ревьюер»: готово"
    subscription = {"id": "claude-code/default", "name": "Claude Code", "locality": "cloud", "prices": {},
                    "capabilities": {"subscription": True}, "model_ref": "default"}  # fmt: skip
    note = rows.picker_items([subscription], set(), None)[1]["note"]
    assert note.startswith("подписка Claude Code")


def test_the_agents_panel(app):
    agents, client = app.agents, app.client
    agents.load()
    wait_until(lambda: agents.profiles and agents.runs)
    assert agents.draft["name"] == "Ревьюер" and agents.draft["saved"] and agents.draft["modelName"] == "Coder"
    assert [t["selected"] for t in agents.toolChoices] == [True, False, False]
    assert agents.projectChoices[0] == {"id": "p1", "name": "LocalChat", "selected": True}

    agents.toggleTool("run_command")
    agents.toggleProject("p1")
    agents.setField("max_steps", "500")  # out of range: kept as it was
    agents.setField("max_steps", "40")
    agents.setField("budget", "0,5")
    agents.save()
    wait_until(lambda: any(e[0] == "save_agent" for e in client.log))
    _, agent_id, body = next(e for e in client.log if e[0] == "save_agent")
    assert agent_id == "a1" and body["tools"] == ["read_file", "run_command"] and body["project_ids"] == []
    assert body["max_steps"] == 40 and body["budget_usd"] == 0.5
    wait_until(lambda: "версия 2" in app.flashText)

    agents.newProfile()
    assert agents.draft["saved"] is False and all(t["selected"] for t in agents.toolChoices)  # [] means all tools
    agents.save()  # no model chosen: the hub's refusal is shown
    wait_until(lambda: "Агент не сохранён" in app.flashText)

    agents.editProfile("a1")
    agents.startRun("Проверь README")
    wait_until(lambda: ("run", "a1", "Проверь README") in client.log)
    wait_until(lambda: agents.runs[0]["task"] == "Проверь README" and agents.runs[0]["status"] == "в очереди")
    agents.cancelRun("r2")
    wait_until(lambda: ("cancel_run", "r2") in client.log)
    send(app, "run.finished", {"run": {**RUN, "status": "error", "detail": "limit"}})
    assert app.flashText == "Агент «Ревьюер»: ошибка" and app.flashError
    agents.openRun("r1")
    assert app.mode == "chats"


# --- stage 5.5: notices outside the window ---


def test_notices_when_the_window_is_behind(qapp):
    from app.ui.notify import Notifier, tray_icon

    front = {"active": False}
    notifier = Notifier(lambda: front["active"])
    assert notifier.notify("Claude Code ждёт подтверждения", "Править hub/chat.py") is True
    front["active"] = True
    assert notifier.notify("Агент «X»: готово", "") is False  # in front, the window says it itself
    assert notifier.shown == [("Claude Code ждёт подтверждения", "Править hub/chat.py")]
    assert not tray_icon().isNull()
    notifier.close()


def test_the_window_notifies_about_requests_and_runs(qapp):
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        quick.notifier._active = lambda: False  # the window behind others
        quick.notifier.quiet = lambda: False  # whatever the time the test runs at: not the quiet hours
        send(app, "approval.requested", {"approval": {**APPROVAL, "id": "ap9", "conversation_id": "other"}})
        send(app, "run.finished", {"run": {**RUN, "status": "done"}})
        assert quick.notifier.shown == [
            ("Claude Code ждёт подтверждения", "Править hub/chat.py · LocalChat"),
            ("Агент «Ревьюер»: готово", "Проверь hub/chat.py"),
        ]
        app.setMode("projects")
        quick.notifier.opened.emit()  # the notice clicked: the window comes forward with that chat and its card
        assert app.mode == "chats" and app.current == "other"
        wait_until(lambda: app.approvals.cardCount == 1 and not app.bg.busy)
        opened = []
        app.uiRequested.connect(opened.append)
        send(app, "approval.requested", {"approval": {**APPROVAL, "id": "ap10", "conversation_id": None}})
        quick.notifier.opened.emit()  # a request with no chat: the shield's list, where its card is
        assert opened == ["approvals"]
    finally:
        quick.close()
        quick.theme.setMode("dark")


# --- stage 6.1: what the hub learnt about models and money ---


def test_model_stats_and_the_forecast(app):
    (row,) = rows.stats_rows([STATS], {"llama-swap/coder": "Coder"})
    assert row["name"] == "Coder" and row["speed"] == "56 запросов · 102.1 т/с · первый токен 0.49 с · ответ 1.65 с"
    assert row["more"] == "ошибки 20% · задачи агентов 2/3" and row["warn"] is True
    calm = rows.stats_rows([{**STATS, "errors": 0, "error_rate": 0.0, "runs": 0, "cost_usd": 0.5}], {})[0]
    assert calm["more"].startswith("без ошибок · $") and not calm["warn"]
    assert rows.forecast_text(None) == ""
    app.loadStats()
    wait_until(lambda: app.statsRows)
    assert app.statsRows[0]["name"] == "Coder"
    assert app.forecastText.startswith("В этом месяце $1.00; последние 7 дней") and "≈ $1.23" in app.forecastText
    assert "Claude Code по подписке — оценка $0.40" in app.forecastText


# --- stage 6.2: advice ---


def test_the_advice_in_the_model_picker(app):
    opened(app)
    wait_until(lambda: app.client.advice_calls)
    wait_until(lambda: app.pickerItems[0].get("section") == "Рекомендуется для этого чата")
    first = app.pickerItems[1]
    assert first["id"] == "llama-swap/coder" and first["note"] == "уже в видеопамяти · 102 т/с здесь · бесплатно"
    opus = next(i for i in app.pickerItems[2:] if i["id"] == "anthropic/claude-opus-5-5")
    assert opus["enabled"] is False and opus["note"] == "недоступна: «LocalChat» — только для локальных моделей (D10)"
    app.setPriority("price")
    wait_until(lambda: app.client.advice_calls[-1][2] == "price")
    assert app.priority == "price" and app.settings.value("chat/priority") == "price"
    app.setPriority("cheapest")  # not a priority: ignored
    assert app.priority == "price"


def test_a_tasks_estimate_in_the_agents_panel(app):
    app.agents.load()
    wait_until(lambda: app.agents.estimate)
    assert app.agents.estimate == "≈ $0.0042 — по 2 прошлым задачам этого агента"


def test_choosing_openrouter_models(app):
    conns = app.connections
    wait_until(lambda: conns.state == "new")
    conns.createPassword("секрет-1", "секрет-1")
    wait_until(lambda: conns.state == "open")
    conns.openModels("openrouter_api_key")
    wait_until(lambda: len(conns.choices) == 2)
    assert conns.choosing == "openrouter_api_key" and conns.pickedCount == 0
    conns.filterModels("deepseek")
    assert [c["id"] for c in conns.choices] == ["deepseek/deepseek-v4-pro"]
    conns.toggleModel("deepseek/deepseek-v4-pro")
    assert conns.pickedCount == 1 and conns.choicesNote == "Выбрано: 1 · найдено: 1"
    conns.saveModels()
    wait_until(lambda: conns.choosing == "")
    assert app.client.chosen == ["deepseek/deepseek-v4-pro"]


def test_choice_rows():
    from app.ui.connections import SHOWN_CHOICES, choice_rows

    models = [{"id": f"vendor/model-{n}", "name": f"Model {n:03d}", "input": 1, "output": 2, "context": 128_000}
              for n in range(100)] + [{"id": "deepseek/deepseek-v4-pro", "name": "DeepSeek V4 Pro", "input": 0.66,
                                       "output": 1.98}]  # fmt: skip
    rows_, total = choice_rows(models, {"vendor/model-99"}, "")
    assert total == 101 and len(rows_) == SHOWN_CHOICES and rows_[0]["id"] == "vendor/model-99"  # picked first
    assert rows_[0]["note"] == "vendor/model-99 · $1 / $2 за 1M · контекст 128K" and rows_[0]["picked"] is True
    found, total = choice_rows(models, set(), "DEEPSEEK pro")
    assert total == 1 and found[0]["name"] == "DeepSeek V4 Pro" and found[0]["note"].endswith("$0.66 / $1.98 за 1M")


def test_dragging_the_window_by_its_bars(qapp):
    """The title bar and the sidebar's top move the window: their drag handlers reach it through their item (a
    handler's own Window.window is null in Qt 6.12)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        window = quick.window
        window.resize(1280, 820)
        QApplication.processEvents()
        for x, y in ((700, 20), (60, 25)):  # the title bar; the sidebar's brand
            QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
            for step in range(1, 12):
                QTest.mouseMove(window, QPoint(x + step * 6, y + step * 2))
                QApplication.processEvents()
            QTest.mouseRelease(
                window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x + 66, y + 22)
            )
        assert [w for w in quick.warnings if "startSystemMove" in w or "Window.window" in w] == []
    finally:
        quick.close()


def test_reloading_the_open_chat_leaves_no_warnings(qapp):
    """After a reconnect the open chat is fetched again; while its rows are replaced, no message fails on a
    null row."""
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        wait_until(lambda: app.messages.rowCount() > 0)
        for _ in range(20):
            QApplication.processEvents()
        app.messages.reset([])  # what a reload does first
        app.openChat(app.current)
        wait_until(lambda: app.messages.rowCount() > 0)
        for _ in range(20):
            QApplication.processEvents()
        assert [w for w in quick.warnings if "Message.qml" in w] == []
    finally:
        quick.close()


def test_unloading_the_local_model_from_the_sidebar_is_asked_first(qapp):
    from PySide6.QtQuick import QQuickItem

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: app.status.get("loaded") == ["coder"])
        sidebar = quick.window.findChild(QQuickItem, "loadedModelCard")
        assert sidebar is not None and sidebar.property("visible")  # coder is in memory: the card shows
        app.askUnloadLocal()
        assert app.question["title"] == "Выгрузить coder?" and app.question["confirm"] == "Выгрузить"
        app.answer(False)
        QApplication.processEvents()
        assert getattr(app.client, "unloaded", 0) == 0  # said no: nothing happens
        app.askUnloadLocal()
        app.answer(True)
        wait_until(lambda: getattr(app.client, "unloaded", 0) == 1)
        app._show_status({**STATUS, "local": {"reachable": True, "running": []}})
        QApplication.processEvents()
        assert not sidebar.property("visible")  # nothing in memory: no card
    finally:
        quick.close()


def test_a_chats_own_instructions_and_preset(app):
    opened(app)
    app.setChatInstructions("  Пиши тесты на pytest. ")
    app.setChatPreset("precise")
    app.setChatPreset("wild")  # not a preset: nothing sent
    wait_until(lambda: not app.bg.busy)
    sent = [entry[2] for entry in app.client.log if entry[0] == "context"]
    # the two go out in the background: in either order
    assert {"instructions": "Пиши тесты на pytest."} in sent[-2:] and {"preset": "precise"} in sent[-2:]
    assert app.contextSettings["preset"] == "precise"


def test_an_older_answer_never_undoes_a_newer_choice(app, monkeypatch):
    """The chat's settings go out in the background and their answers come back in any order: the answer to an
    earlier change, arriving last, used to replace all of them - a later choice was lost (the full run of 13.6)."""
    opened(app)
    sent = []
    monkeypatch.setattr(app, "_call", lambda fn, on_done=None, on_error=None: sent.append((fn, on_done)) or True)
    app.setChatInstructions("Пиши тесты")
    app.setChatPreset("precise")
    app.setChatPreset("creative")
    answers = [(fn(), on_done) for fn, on_done in sent]  # the hub answered each with what it saved
    for settings, on_done in reversed(answers):  # the earliest answer comes back last
        on_done(settings)
    assert app.contextSettings["preset"] == "creative" and app.contextSettings["instructions"] == "Пиши тесты"


def test_a_model_writing_russian_with_errors_does_not_write_cards():
    """25.5: the user's mark in a model's settings; the cards' writer passes such a model by - the one of the role
    «cards» excepted (the user chose it), and one is taken all the same when no other local one is."""
    from types import SimpleNamespace

    from app.ui import model_setup as ms
    from app.ui.projects import Projects

    assert ms.settings_of({**ms.BLANK, "weak_russian": True}, []) == {"weak_russian": True}
    assert ms.draft_of({"weak_russian": True})["weak_russian"] is True and ms.settings_of(dict(ms.BLANK), []) == {}
    coder = {"id": "l/coder", "name": "Coder", "locality": "local", "weak_russian": True}
    qwen = {"id": "l/qwen", "name": "Qwen", "locality": "local"}
    opus = {"id": "c/opus", "name": "Opus", "locality": "cloud"}
    assert "пишет по-русски с ошибками" in next(i["note"] for i in ms.choices([coder], "") if i["id"] == "l/coder")

    def writer(chosen, models, role=""):
        app = SimpleNamespace(role_model=lambda _r: role, models={m["id"]: m for m in models}, model_list=models,
                              modelId=chosen)  # fmt: skip
        return Projects._card_writer(SimpleNamespace(app=app))

    assert writer("l/coder", [opus, coder, qwen]) == "l/qwen"  # the chosen one writes Russian with errors
    assert writer("c/opus", [opus, coder, qwen]) == "l/qwen"
    assert writer("l/coder", [opus, coder]) == "l/coder"  # no other local one: it all the same
    assert writer("c/opus", [opus]) == "c/opus"
    assert writer("l/qwen", [coder, qwen], role="l/coder") == "l/coder"  # the role is the user's own choice


def test_roles_in_the_window(app):
    from app.ui import model_setup as ms

    assert rows.fallback_note(None) == ""
    assert rows.fallback_note({"from_name": "Opus", "reason": "503\n overloaded"}) == (
        "Ответила запасная модель: «Opus» не ответила — 503 overloaded"
    )
    app.client.model_saved[""] = [{"roles": {"chat": "llama-swap/coder", "cards": "anthropic/claude-opus-5-5",
                                             "agents": "llama-swap/coder", "fallback": "gone/model"}}]  # fmt: skip
    app.load_models()
    wait_until(lambda: not app.bg.busy and app.roles)
    assert app.role_model("fallback") == ""  # not offered now: no role here
    app.chooseModel("anthropic/claude-opus-5-5")
    app.newChat()
    assert app.modelId == "llama-swap/coder"  # the model for new chats
    assert app.projects._card_writer() == "anthropic/claude-opus-5-5"
    app.agents.newProfile()
    assert app.agents.draft["model_id"] == "llama-swap/coder"
    setup = app.model_setup
    setup.open("*")
    wait_until(lambda: not app.bg.busy and setup.general and not setup.loading)
    rows_ = {r["id"]: r for r in setup.roleRows}
    assert rows_["chat"]["modelName"] == "Coder" and rows_["fallback"]["modelName"] == "gone/model — сейчас нет"
    assert rows_["vision"]["modelName"] == "не задана" and setup.roleChoices[0]["name"] == "не задана"
    setup.setRole("fallback", "anthropic/claude-opus-5-5")
    setup.setRole("cards", "")
    setup.save()
    wait_until(lambda: not app.bg.busy and not setup.dirty)
    saved = next(c for c in reversed(app.client.model_calls) if c[0] == "save")
    assert saved[1:] == ("", {"roles": {"chat": "llama-swap/coder", "agents": "llama-swap/coder",
                                        "fallback": "anthropic/claude-opus-5-5"}})  # fmt: skip
    assert ms.settings_of({**ms.BLANK, "roles": {"chat": ""}}, [], general=True) == {}


def test_tuning_by_measurement_in_the_models_sheet(app):
    """«Модели → Запуск → Подбор по замерам» (15.2, D49): the line says what goes on - measuring, checking, waiting
    for the chats, queued - or what was found; the buttons ask the hub; a model tuned is said once."""
    from app.ui import model_setup as ms

    state = {"job": {"status": "idle", "queue": []}, "results": {}, "auto": True}
    assert "замерит на этом компьютере" in ms.tune_text(state, "big")
    measuring = {"status": "measuring", "model_id": "big", "done": 3, "total": 12}
    assert ms.tune_text({**state, "job": measuring}, "big") == ("Меряю: 3 из 12… На это время модели выгружены из "
                                                                "видеопамяти.")  # fmt: skip
    assert ms.tune_text({**state, "job": {"status": "waiting", "model_id": "big"}}, "big").startswith("Жду")
    assert ms.tune_text({**state, "job": {"status": "idle", "queue": ["big"]}}, "big") == "В очереди на замер."
    better = {"status": "better", "at": "2026-11-04T20:00:00Z", "before": {"tg": 40.2, "pp": 900.0},
              "after": {"tg": 52.4, "pp": 1500.0},
              "settings": {"flash_attn": "on", "cache_type": "q8_0", "ubatch": 2048, "threads": 16}}  # fmt: skip
    assert ms.tune_text({**state, "results": {"big": better}}, "big").endswith(
        "ответ 40 → 52 ток/с, чтение подсказки 900 → 1500 ток/с (flash attention вкл, KV-кэш q8_0, ubatch 2048, "
        "16 потоков).")  # fmt: skip
    same = {"status": "same", "at": "2026-11-04T20:00:00Z", "after": {"tg": 51.0}}
    assert "и так самые быстрые — ответ 51 ток/с" in ms.tune_text({**state, "results": {"big": same}}, "big")

    app._set_models([*MODELS, {"id": "llama-swap/big", "model_ref": "big", "name": "Big", "locality": "local",
                               "provider": "llama-swap", "prices": {}}])  # fmt: skip
    setup = app.model_setup
    setup.open("llama-swap/big")
    wait_until(lambda: not app.bg.busy and setup.launch.get("editable"))
    assert setup.tuneAuto and not setup.tuneBusy and "замерит" in setup.tuneText
    setup.tune()
    wait_until(lambda: ("tune", "big") in app.client.tune_calls and setup.tuneBusy)
    send(app, "tune.changed", {"job": {**measuring, "done": 5, "queue": []}})
    assert setup.tuneText.startswith("Меряю: 5 из 12")
    app.client.tune_status = {"job": {"status": "idle", "queue": []}, "results": {"big": better}, "auto": True}
    send(app, "tune.changed", {"job": {"status": "idle", "queue": []}})
    wait_until(lambda: setup.tuneTone == "ok")
    assert not setup.tuneBusy and "«big» подобрана по замерам: ответ 40 → 52 ток/с" in app._flash
    setup.setTuneAuto(False)
    wait_until(lambda: ("auto", False) in app.client.tune_calls and not setup.tuneAuto)
    setup.tuneAll()
    wait_until(lambda: ("all",) in app.client.tune_calls)


def test_a_local_models_launch_in_the_window(app):
    from app.ui import model_setup as ms

    big = LLAMA[1]
    draft = ms.launch_draft(big)
    assert draft == {"context": "32768", "gpu_mode": "all", "gpu_n": "", "flash_attn": "on", "cache_type": "q8_0",
                     "parallel": "1", "ttl_min": "0"}  # fmt: skip
    assert ms.launch_changes(draft, big) == {}
    changed = {**draft, "context": "65536", "gpu_mode": "n", "gpu_n": "40", "ttl_min": "15", "cache_type": "q4_0"}
    assert ms.launch_changes(changed, big) == {"ttl": 900, "context": 65536, "gpu_layers": 40, "cache_type": "q4_0"}
    assert ms.launch_changes({**draft, "context": "65536", "ttl_min": "5"}, LLAMA[0]) == {"ttl": 300}  # Strata: ttl
    with pytest.raises(ValueError, match="не число"):
        ms.launch_changes({**draft, "context": "много"}, big)
    assert "помещается в видеопамять целиком" in ms.launch_estimate(draft, big, 16384)
    assert "не поместится" in ms.launch_estimate({**draft, "context": "1048576", "cache_type": "f16"}, big, 16384)
    assert ms.launch_estimate(draft, {**big, "info": None, "file_error": "нет файла"}, 16384).endswith("нет файла")

    app._set_models([*MODELS, {"id": "llama-swap/big", "model_ref": "big", "name": "Big", "locality": "local",
                               "provider": "llama-swap", "prices": {}}])  # fmt: skip
    setup = app.model_setup
    setup.open("llama-swap/coder")
    wait_until(lambda: not app.bg.busy and setup.launch.get("engine") == "strata")
    assert not setup.launch["editable"] and setup.launchEstimate == ""
    setup.open("anthropic/claude-opus-5-5")
    wait_until(lambda: not app.bg.busy and not setup.loading)
    assert setup.launch == {}  # a cloud model: nothing to launch
    setup.open("llama-swap/big")
    wait_until(lambda: not app.bg.busy and setup.launch.get("engine") == "llama.cpp")
    assert not setup.launchDirty and "целиком" in setup.launchEstimate
    setup.setLaunch("context", "262144")  # its KV cache alone 9 GB: what does not fit goes to RAM
    setup.setLaunch("gpu_mode", "auto")
    assert setup.launchDirty and "сам положит" in setup.launchEstimate
    setup.saveLaunch()
    wait_until(lambda: not app.bg.busy and not setup.launchBusy)
    launched = next(c for c in reversed(app.client.model_calls) if c[0] == "launch")
    assert launched == ("launch", "big", {"context": 262144, "gpu_layers": "auto"})
    assert "llama-swap перечитал" in setup.launchStatus and not setup.launchDirty
    setup.trialLaunch()
    wait_until(lambda: not app.bg.busy and not setup.launchBusy)
    assert "не запустилась — конфиг возвращён из config.yaml.bak-1" in setup.launchStatus
    over = "≈ 23.4 ГБ видеопамяти из 15.9 (KV-кэш 19.1 ГБ) — не поместится в видеопамять: меньше контекст"
    app.client.trial_llama_model = lambda model_id: {"ok": True, "text": "на связи", "tok_s": 16.2, "seconds": 18.0,
                                                     "over": over}  # fmt: skip
    setup.trialLaunch()  # it answered - from the shared memory the driver lent it (stage 28)
    wait_until(lambda: not app.bg.busy and not setup.launchBusy)
    assert "«на связи»" in setup.launchStatus and "общей памяти" in setup.launchStatus and over in setup.launchStatus
    assert setup.launchFailed


def test_budgets_and_settings_files_in_the_window(app, tmp_path):
    from app.ui import model_setup as ms

    draft = ms.draft_of({"context_budget": {"map": 500, "file_lines": 600}})
    assert (draft["budget_map"], draft["budget_file_lines"], draft["budget_live"]) == ("500", "600", "")
    assert ms.settings_of(draft, [])["context_budget"] == {"map": 500, "file_lines": 600}
    with pytest.raises(ValueError, match="целое"):
        ms.settings_of({**draft, "budget_map": "1.5"}, [])
    app.client.exported = {"kind": "localchat-model-settings", "models": {"llama-swap/coder": {"name": "К"}}}
    target = tmp_path / "models.json"
    app.model_setup.exportTo(target.as_uri())
    wait_until(lambda: not app.bg.busy and target.exists())
    assert json.loads(target.read_text(encoding="utf-8"))["models"] == {"llama-swap/coder": {"name": "К"}}
    assert "записаны в models.json" in app.flashText
    app.model_setup.importFrom(target.as_uri())
    wait_until(lambda: not app.bg.busy and app.client.imported)
    assert app.client.imported[-1]["models"] == {"llama-swap/coder": {"name": "К"}}
    wait_until(lambda: "Загружено: 1" in app.flashText)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    app.model_setup.importFrom((tmp_path / "broken.json").as_uri())
    assert "Файл не прочитан" in app.flashText


def test_all_the_settings_into_a_file_and_back_after_a_look(app, tmp_path):
    """D46, 13.10: the window adds its own part to the hub's file; a file back shows what it would do, part by part,
    a part can be left out (and the rest looked at again), the password opens the parts behind it, and loading
    reloads the window."""
    transfer = app.transfer_view
    app.settings.setValue("ui/theme", "dark")
    target = tmp_path / "all.json"
    transfer.exportTo(target.as_uri())
    wait_until(lambda: not app.bg.busy and target.exists())
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["window"]["ui/theme"] == "dark" and "похожее на ключи вырезано: 1" in app.flashText
    assert saved["window"]["terminals/open"] == "false" and "quick/geometry" not in saved["window"]  # 25.4: the
    # layout goes, as the window has it, not where the window stood
    saved["window"] = {"ui/theme": "light", "chat/priority": "speed", "chat/model": "nope/x", "ui/sidebarWidth": 380,
                       "projects/chatsHeight": "0", "terminals/height": "450", "terminals/open": "true",
                       "quick/maximized": "maybe"}  # fmt: skip
    target.write_text(json.dumps(saved), encoding="utf-8")

    assert transfer.openFile(target.as_uri()) and transfer.fileName == "all.json"
    wait_until(lambda: not transfer.busy and transfer.parts)
    parts = {p["id"]: p for p in transfer.parts}
    assert list(parts) == ["projects", "mcp", "window"] and app.client.settings_calls[-1] == ("preview", None, "")
    assert parts["mcp"]["locked"] and transfer.needsPassword
    assert "придёт выключенным" in parts["mcp"]["items"][0]["warn"]
    window = {i["what"]: (i["actionText"], i["detail"]) for i in parts["window"]["items"]}
    assert window == {"тема": ("изменится", "светлая"), "подбор модели: что важнее": ("изменится", "скорость"),
                      "модель нового чата": ("пропущено", "такой модели нет в этом хабе"),
                      "ширина боковой панели": ("изменится", "380"),
                      "высота «Чаты проекта»": ("без изменений", "по размеру чатов"),
                      "высота панели терминалов": ("изменится", "450"),
                      "панель терминалов открыта": ("изменится", "да"),
                      "окно во весь экран": ("пропущено", "неизвестное значение «maybe»")}  # fmt: skip
    transfer.toggle("projects")
    wait_until(lambda: not transfer.busy and app.client.settings_calls[-1] == ("preview", ["mcp"], ""))
    assert {p["id"]: (p["chosen"], p["counts"]) for p in transfer.parts}["projects"] == (False, "не загружается")
    transfer.toggle("projects")
    wait_until(lambda: not transfer.busy and app.client.settings_calls[-1][1] == ["mcp", "projects"])
    app.client.set_connections_password("пароль подключений")
    transfer.unlock("не тот")
    wait_until(lambda: not transfer.busy and transfer.error)
    assert "неверный пароль" in transfer.error and transfer.needsPassword
    transfer.unlock("пароль подключений")
    wait_until(lambda: not transfer.busy and not transfer.needsPassword)
    assert app.client.settings_calls[-1] == ("preview", ["mcp", "projects"], "ticket-1")

    finished = []
    transfer.finished.connect(lambda: finished.append(True))
    transfer.apply()
    wait_until(lambda: finished and not app.bg.busy)
    assert app.client.settings_calls[-1] == ("import", ["mcp", "projects"], "ticket-1")
    assert app.settings.value("ui/theme") == "light" and app.priority == "speed"
    assert (app.sidebarWidth, app.terminals.panelHeight, app.terminals.panelOpen) == (380, 450, True)
    assert "Настройки загружены: добавлено 2, изменено 5, пропущено 2" in app.flashText
    assert ("lock", "ticket-1") in app.client.secret_calls and transfer.fileName == "" and not transfer.parts

    (tmp_path / "models.json").write_text(json.dumps({"kind": "localchat-model-settings"}), encoding="utf-8")
    assert not transfer.openFile((tmp_path / "models.json").as_uri()) and "Настройке моделей" in app.flashText


def test_the_agent_modes_rows():
    from app.ui import autonomy as au

    rows_ = au.task_rows([AUTO_RUN, {**AUTO_RUN, "id": "old", "status": "done", "shown_status": "done",
                                     "report": {"goal_met": True}}, RUN], "auto1", {"auto1"})  # fmt: skip
    assert [(r["id"], r["status"], r["section"], r["selected"]) for r in rows_] == [
        ("auto1", "paused", "Идут", True),
        ("old", "done", "Готовы", False),
    ]  # an ordinary run: not a task here
    assert rows_[1]["goalMet"] and rows_[0]["statusLabel"] == "пауза"
    steps = au.step_rows(AUTO_PLAN["plans"][0])
    assert (steps[0]["label"], steps[0]["icon"], steps[1]["label"]) == ("готово", "check", "идёт · попыток 1")
    # 25.1: what a step changed by the hub's diff, and what of it breaks the condition «готово»
    assert (steps[0]["files"], steps[1]["files"], steps[1]["mismatch"]) == ("изменено: README.md (+1 −0)", "", "")
    assert steps[0]["mismatch"] == "условие «не трогать: README.md» — изменён README.md"
    assert au.files_text([]) == "файлы не менялись" and au.files_text(None) == ""
    many = [{"path": f"f{i}.txt", "status": "added", "added": 2, "removed": 0} for i in range(12)]
    assert au.files_text(many).endswith("f9.txt (+2 −0) и ещё 2")
    said = au.report_rows({"summary": "ok", "goal_met": False, "files": ["a.py"], "changed": ["README.md"],
                           "unlisted": ["README.md"], "unchanged": ["a.py"], "broken": ["условие …"]})  # fmt: skip
    assert (said["changed"], said["unlisted"], said["unchanged"], said["broken"]) == (
        ["README.md"], ["README.md"], ["a.py"], ["условие …"])  # fmt: skip
    assert au.report_rows({"summary": "ok", "goal_met": True})["unlisted"] == []  # a report from before 25.1
    text = au.plan_text(AUTO_PLAN["plans"][0])
    assert text == "Прочитать — файл прочитан\nПосчитать — дважды"
    assert au.steps_from_text("1. Прочитать — файл прочитан\n\n2) Посчитать") == [
        {"title": "Прочитать", "check": "файл прочитан"},
        {"title": "Посчитать", "check": ""},
    ]
    assert au.report_rows({"summary": "ok", "goal_met": False, "not_done": ["x"]})["verdict"] == "Цель не достигнута"
    good = au.goal_body({**au.BLANK, "goal": " Сделать ", "model_id": "m", "budget_usd": "0,5"})
    assert good["goal"] == "Сделать" and good["budget_usd"] == 0.5 and good["max_steps"] == 100
    for change, said in (
        ({"goal": " "}, "Напишите цель"),
        ({"model_id": ""}, "Выберите модель"),
        ({"max_steps": "много"}, "Шагов: нужно целое"),
        ({"budget_usd": "x"}, "Бюджет"),
    ):
        with pytest.raises(ValueError, match=said):
            au.goal_body({**au.BLANK, "goal": "x", "model_id": "m", **change})


def test_the_agent_mode_in_the_window(qapp):
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.run_list = [dict(AUTO_RUN), dict(RUN)]
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.autonomy_view.tasks)
        view = app.autonomy_view
        app.setMode("agent")
        view.newTask()
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "agentForm").property("visible")
        assert view.draft["model_id"] == app.modelId  # no executor role: the chat's model
        view.applyTemplate("tests")
        assert view.draft["done_when"].startswith("все тесты проходят")
        view.setField("goal", "Починить тесты в LocalChat")
        view.setField("autonomy", "each_step")
        view.start()
        wait_until(lambda: not app.bg.busy and view.selectedId == "auto2")
        started = client.auto_calls[0]
        assert started[0] == "start" and started[1]["goal"] == "Починить тесты в LocalChat"
        assert started[1]["autonomy"] == "each_step" and started[1]["max_minutes"] == 60

        view.select("auto1")
        wait_until(lambda: not app.bg.busy and view.task.get("planVersion") == 2)
        task = view.task
        assert (task["progress"], task["planReason"], task["question"]["question"]) == (
            "1 из 2",
            "сбивался",
            "Считать пустые?",
        )
        # a run's error in plain words above its technical text (2026-10-28: an 8K model's context ran out)
        run = next(r for r in view._runs if r["id"] == "auto1")
        run["detail"] = "llama-swap: HTTP 400: request (15961 tokens) exceeds the available context size (8192 tokens)"
        assert view.task["detailPlain"].startswith("Разговор не помещается в модель — ")
        assert "Запуск в llama-swap → Контекст" in view.task["detailPlain"]
        run["detail"] = ""
        assert view.task["detailPlain"] == ""
        assert quick.window.findChild(QObject, "agentQuestion").property("visible")
        mismatch = item_named(quick.window.findChild(QObject, "agentSteps"), "agentStepMismatch")  # 25.1: step 1's
        assert mismatch.property("visible") and "не трогать: README.md" in mismatch.property("text")
        view.answer("q1", "  нет  ")
        view.pause()
        view.editPlan("Только посчитать — дважды", "быстрее")
        wait_until(lambda: not app.bg.busy and len(client.auto_calls) == 4)
        expected = [
            ("answer", "auto1", "q1", "нет"),
            ("pause", "auto1"),
            ("plan", "auto1", [{"title": "Только посчитать", "check": "дважды"}], "быстрее"),
        ]
        assert sorted(client.auto_calls[1:], key=str) == sorted(expected, key=str)  # in the background: any order
        # its approval comes as the card over the Agent view (D38)
        send(app, "approval.requested", {"approval": {**APPROVAL, "id": "ap7", "conversation_id": "auto1",
                                                      "tool": "set_plan", "input": {"version": 2}}})  # fmt: skip
        assert app.approvals.card.get("title") == "Утвердить план (версия 2)" and app.approvals.cardCount == 1
        app.approvals.openChat("auto1")  # the shield's list: a task opens in the Agent mode
        assert app.mode == "agent" and view.selectedId == "auto1"
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_agents_memory_in_the_window(qapp):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui import memory_view
    from app.ui.main import QuickApp

    rows_ = memory_view.fact_rows(FakeClient().fact_list)
    assert rows_[0]["meta"].startswith("отчёты · агент · ") and rows_[1]["meta"].startswith("вы · ")
    assert memory_view.skill_rows(FakeClient().skill_list)[0]["steps"] == "1. Прочитать" + chr(10) + "2. Посчитать"
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        sheet = quick.window.findChild(QObject, "memorySheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened") and len(app.memory_view.facts) == 2)
        app.memory_view.search("почта")
        wait_until(lambda: not app.bg.busy and len(app.memory_view.facts) == 1)
        app.memory_view.setFlag(2, "personal", False)
        app.memory_view.saveFact(2, "  Почта: ivan@mail.ru ")
        app.memory_view.deleteFact(1)
        app.memory_view.addFact("Сервер — на 8000", "", False)
        app.memory_view.keepSkill("auto1", "Подсчёт строк")
        wait_until(lambda: not app.bg.busy and len(client.memory_calls) == 5)
        assert sorted(client.memory_calls, key=str) == sorted([
            ("change", 2, {"personal": False}), ("change", 2, {"text": "Почта: ivan@mail.ru"}), ("delete", 1),
            ("add", "Сервер — на 8000", False), ("skill", "auto1", "Подсчёт строк")], key=str)  # fmt: skip
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_folders_and_sites_for_the_agent_in_the_window(qapp, tmp_path):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.autonomy_view
        sheet = quick.window.findChild(QObject, "agentAccessSheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened") and view.accessFolders)
        assert view.domainsText == "pypi.org"
        view.addFolder(tmp_path.as_uri())
        wait_until(lambda: not app.bg.busy and len(view.accessFolders) == 2)
        view.setFolderWrite(view.accessFolders[-1]["path"], True)  # the path as the hub keeps it
        wait_until(lambda: not app.bg.busy and view.accessFolders[-1]["write"])
        view.setDomains("pypi.org, api.example.com\n docs.python.org")
        view.resetDomains()
        wait_until(lambda: not app.bg.busy and len(client.access_calls) == 4)
        assert client.access_calls[0]["folders"][-1] == {"path": tmp_path.as_posix(), "write": False}
        assert {"domains": ["pypi.org", "api.example.com", "docs.python.org"]} in client.access_calls
        assert client.access_calls[-1] == {"domains": ["docs.python.org", "pypi.org"]}
        # the cloud (D46): a folder a cloud model may read, the windows of programs - both off until turned on
        assert not view.screenCloud and not view.accessFolders[-1].get("cloud")
        view.setFolderCloud(view.accessFolders[-1]["path"], True)
        wait_until(lambda: not app.bg.busy and view.accessFolders[-1].get("cloud"))
        assert view.accessFolders[-1]["write"]  # the folder's writing kept
        switch = quick.window.findChild(QObject, "accessScreenCloud")
        switch.toggle()
        QMetaObject.invokeMethod(switch, "toggled")
        wait_until(lambda: not app.bg.busy and view.screenCloud)
        assert client.access_calls[-1] == {"screen_cloud": True}
        view.newTask()
        view.setFlag("network", True)
        view.setField("goal", "Скачать курс валют")
        view.start()
        wait_until(lambda: not app.bg.busy and client.auto_calls)
        assert client.auto_calls[0][1]["network"] is True
        # the windows are the task's (22.3): a new task starts with the switch; the task may say otherwise
        app.setMode("agent")
        view.newTask()
        wait_until(lambda: not app.bg.busy and view.draft["screenCloud"] is True)
        assert quick.window.findChild(QObject, "agentScreenCloud") is not None
        view.setFlag("screen_cloud", False)
        assert view.draft["screenCloud"] is False
        view.setField("goal", "Прочитать заметки")
        view.start()
        wait_until(lambda: not app.bg.busy and len(client.auto_calls) == 2)
        assert client.auto_calls[1][1]["screen_cloud"] is False
        assert client.auto_calls[0][1]["screen_cloud"] is None  # not said: the switch, as the hub has it
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_outside_mcp_servers_in_connections(qapp):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp
    from app.ui.mcp_view import env_lines

    assert env_lines("TOKEN=a=b; OWNER = Анна;\n X=1 ") == "TOKEN=a=b\nOWNER = Анна\nX=1"
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        mcp = app.mcp_view
        sheet = quick.window.findChild(QObject, "connectionsSheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened") and mcp.servers)
        assert mcp.servers[0]["status"] == "ошибка" and mcp.servers[0]["detail"] == "FileNotFoundError: nope"
        mcp.add("notes", "python server.py", "OWNER=Анна")  # locked: the hub refuses, the sheet asks the password
        wait_until(lambda: not mcp.busy and mcp.error)
        assert client.mcp_calls == []
        app.connections.createPassword("секрет-1", "секрет-1")
        wait_until(lambda: app.connections.state == "open")
        mcp.add(" notes ", " python server.py ", "OWNER=Анна; TOKEN=x")
        wait_until(lambda: not mcp.busy and len(mcp.servers) == 2)
        assert client.mcp_calls[0] == ("add", "notes", "python server.py", "OWNER=Анна\nTOKEN=x")
        notes = next(s for s in mcp.servers if s["name"] == "notes")
        assert notes["opened"] and notes["status"] == "работает · инструментов: 2" and notes["env"] == "OWNER, TOKEN"
        assert notes["tools"][0] == {"tool": "list_notes", "description": "Заметки.", "cls": "read",
                                     "clsText": "только читает", "changed": False}  # fmt: skip
        mcp.setClass("notes", "wipe_notes", "write")
        wait_until(lambda: not mcp.busy and next(s for s in mcp.servers if s["name"] == "notes")["tools"][1]["changed"])
        mcp.setClass("notes", "wipe_notes", "nonsense")  # not a class: nothing sent
        mcp.setEnabled("notes", False)
        notes = lambda: next(s for s in mcp.servers if s["name"] == "notes")  # noqa: E731
        wait_until(lambda: not mcp.busy and notes()["status"] == "выключен")
        mcp.restart("broken")
        mcp.remove("notes")
        assert app.question["title"] == "Удалить сервер MCP «notes»?"
        app.answer(True)
        wait_until(lambda: not mcp.busy and len(mcp.servers) == 1)
        assert [c[0] for c in client.mcp_calls] == ["add", "class", "set", "restart", "delete"]
        assert quick.window.findChild(QObject, "mcpSection") is not None
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_agent_at_the_desktop_frame_and_stop(qapp):
    from PySide6.QtCore import QObject

    from app.ui.computer_view import logical_rect
    from app.ui.main import QuickApp

    screens = [(0, 0, 1280, 720, 1.5), (1920, 0, 1920, 1080, 1.0)]  # a 150% laptop, a 100% monitor to its right
    assert logical_rect(300, 150, 600, 450, screens) == [200, 100, 400, 300]
    assert logical_rect(2000, 100, 800, 600, screens) == [2000, 100, 800, 600]
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.computer_view
        frame = quick.window.findChild(QObject, "agentFrame")
        bar = quick.window.findChild(QObject, "agentBar")
        assert frame is not None and bar is not None and not frame.property("visible")
        places = {42: (100, 120, 360, 420)}
        view.place = lambda hwnd: places.get(hwnd)  # where the window is now (the real one asks Windows)
        window = {"id": 42, "title": "Тестовое окно агента", "rect": [100, 120, 360, 420]}
        control = {"run_id": "r1", "window": window, "program": "Python (тест)", "action": "type"}
        app.on_event({"type": "computer.control", "payload": control})
        assert view.active and view.text == "Агент управляет компьютером · Python (тест)"
        wait_until(lambda: frame.property("visible") and bar.property("visible"))
        start = frame.property("x")
        places[42] = (400, 120, 500, 420)  # the person moves and widens the window between the agent's steps
        wait_until(lambda: frame.property("x") != start)  # the frame follows by itself, no new step needed
        assert view.frame[2] > 360 / 2  # wider too
        del places[42]  # minimized (or gone): no frame around nothing; the bar and «Стоп» stay
        wait_until(lambda: not frame.property("visible") and bar.property("visible"))
        places[42] = (100, 120, 360, 420)
        wait_until(lambda: frame.property("visible"))
        assert quick.notifier.stop_shown
        view.stop()
        wait_until(lambda: ("cancel_run", "r1") in client.log)
        assert app.flashText.startswith("Агент остановлен")
        app.on_event({"type": "computer.released", "payload": {"run_id": "r1"}})
        assert not view.active and not quick.notifier.stop_shown
        wait_until(lambda: not frame.property("visible"))
        app.on_event({"type": "computer.paused", "payload": {"run_id": "r1", "why": "person"}})
        assert app.flashText.startswith("Агент ждёт")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_schedules_in_the_window(qapp, tmp_path):
    from datetime import datetime

    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp
    from app.ui.schedule_view import trigger_text, when_text

    assert trigger_text({"kind": "time", "every": "weekdays", "at": "09:00", "days": [1, 5]}) == "по пн, пт в 09:00"
    assert trigger_text({"kind": "telegram", "contains": ""}) == "любое сообщение боту"
    now = datetime(2026, 10, 28, 12, 0)
    assert when_text({"enabled": True, "next_at": "2026-10-29T09:00"}, now) == "завтра в 09:00"
    assert when_text({"enabled": True, "waiting_until": "2026-10-29T08:00"}, now) == "ждёт конца тихих часов (08:00)"
    assert when_text({"enabled": True, "next_at": None}, now) == "ждёт события"
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("agent")
        view, schedules = app.autonomy_view, app.schedules_view
        view.newTask()
        view.setField("goal", "Разобрать новые счета")
        assert quick.window.findChild(QObject, "schedulePanel") is not None
        schedules.setField("when", "event")
        schedules.create()
        assert schedules.error == "Выберите папку, в которой ждать новые файлы"
        schedules.setField("folder", str(tmp_path))
        schedules.setField("pattern", "*.pdf")
        schedules.setComputer(True)
        schedules.create()
        wait_until(lambda: not app.bg.busy and schedules.items)
        _kind, body = client.schedule_calls[0]
        assert body["trigger"] == {"kind": "file", "folder": str(tmp_path), "pattern": "*.pdf"}
        assert body["computer"] is True and body["goal"]["goal"] == "Разобрать новые счета"
        assert app.flashText.startswith("Запланировано: новый файл *.pdf в ")
        assert schedules.draft["when"] == "now"  # the form is back to «now»
        schedules.setField("when", "time")
        schedules.setField("every", "weekdays")
        schedules.toggleDay(1)  # Monday off
        schedules.toggleDay(6)  # Saturday on
        schedules.setField("at", "7:45")
        schedules.create()
        wait_until(lambda: not app.bg.busy and len(schedules.items) == 2)
        assert client.schedule_calls[1][1]["trigger"] == {"kind": "time", "every": "weekdays", "at": "7:45",
                                                          "days": [2, 3, 4, 5, 6]}  # fmt: skip
        row = schedules.items[0]
        assert row["trigger"].startswith("новый файл *.pdf") and row["computer"] is True
        schedules.setEnabled("s1", False)
        wait_until(lambda: not app.bg.busy and not schedules.items[0]["enabled"])
        assert schedules.items[0]["when"] == "выключено"
        schedules.runNow("s2")
        wait_until(lambda: ("run", "s2") in client.schedule_calls)
        schedules.remove("s1")
        assert app.question["title"] == "Удалить расписание «Разобрать новые счета»?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and len(schedules.items) == 1)
        schedules.setQuiet(True, "полночь", "08:00")
        wait_until(lambda: not app.bg.busy and app.flashError)
        schedules.setQuiet(False, "22:00", "07:00")
        wait_until(lambda: not app.bg.busy and schedules.quiet["enabled"] is False)
        assert schedules.quietText.startswith("Тихих часов нет")
        notifier = quick.notifier  # the quiet hours: notices wait for the morning
        notifier.quiet = lambda: True
        notifier._active = lambda: False
        assert notifier.notify("Агент спрашивает", "Какой счёт?") is False and notifier.held
        notifier.quiet = lambda: False
        notifier.flush()
        assert notifier.shown[-1] == ("Агент спрашивает", "Какой счёт?") and notifier.held == []
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_agents_mailbox_in_the_window(qapp):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.approvals import approval_rows
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        mail = app.mail_view
        sheet = quick.window.findChild(QObject, "connectionsSheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened"))
        assert quick.window.findChild(QObject, "mailSection") is not None and not mail.connected
        assert mail.knownProvider("anna@ya.ru") and not mail.knownProvider("anna@example.org")
        mail.save("anna@ya.ru", "app-pass", "", "", "", "")  # locked: refused
        wait_until(lambda: not mail.busy and mail.note)
        assert not mail.noteOk and client.mail_calls == []
        app.connections.createPassword("секрет-1", "секрет-1")
        wait_until(lambda: app.connections.state == "open")
        mail.save(" anna@ya.ru ", "app-pass", "", "", "", "")
        wait_until(lambda: not app.bg.busy and mail.connected)
        assert client.mail_calls[0] == ("set", {"address": "anna@ya.ru", "password": "app-pass"})
        assert mail.servers == "IMAP imap.yandex.ru:993 · SMTP smtp.yandex.ru:465"
        mail.check()
        wait_until(lambda: not mail.busy and mail.note.startswith("почта работает"))
        mail.remove()
        assert app.question["title"] == "Отключить почту?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and not mail.connected)
        schedules = app.schedules_view  # a new letter as the start of a task
        app.setMode("agent")
        app.autonomy_view.newTask()
        app.autonomy_view.setField("goal", "Разобрать счёт из письма")
        schedules.setField("when", "event")
        schedules.setField("event", "mail")
        schedules.setField("words", "счёт")
        schedules.create()
        wait_until(lambda: not app.bg.busy and schedules.items)
        assert client.schedule_calls[-1][1]["trigger"] == {"kind": "mail", "words": "счёт"}
        assert schedules.items[0]["trigger"] == "новое письмо со словами «счёт»"
        letter = {
            "id": "a1",
            "tool": "mail_send",
            "kind": "network",
            "network": True,
            "class": "",
            "input": {"to": ["buh@example.org"], "subject": "Re: Счёт"},
            "preview": {"kind": "text", "text": "Кому: buh@example.org", "note": "письмо от anna@ya.ru"},
        }
        row = approval_rows([letter], projects={}, chats={})[0]
        assert row["title"] == "Отправить письмо: Re: Счёт" and row["alwaysAsk"] == "send"
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_agents_api_keys_in_the_window(qapp):
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        keys = app.agent_keys
        sheet = quick.window.findChild(QObject, "connectionsSheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened"))
        assert quick.window.findChild(QObject, "keysSection") is not None
        keys.save("weather", "api.weather.example", "X-Api-Key", "", "w-1")  # locked: refused
        wait_until(lambda: not app.bg.busy and keys.error)
        app.connections.createPassword("секрет-1", "секрет-1")
        wait_until(lambda: app.connections.state == "open")
        keys.save(" Weather ", "", "", "", "")
        assert keys.error.startswith("Нужны имя ключа и сайт")
        keys.save(" Weather ", " api.weather.example ", "", "Bearer ", " w-1 ")
        wait_until(lambda: not app.bg.busy and keys.items)
        assert client.key_calls == [("weather", {"host": "api.weather.example", "header": "Authorization",
                                                 "prefix": "Bearer ", "value": "w-1"})]  # fmt: skip
        assert keys.items[0]["note"] == "Authorization: Bearer …" and "w-1" not in str(keys.items)
        keys.remove("weather")
        assert app.question["title"] == "Удалить ключ «weather»?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and not keys.items)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_coordinators_board_in_the_window(qapp):
    """16.4 (D50): the open coordinator's tasks - a bar over the composer, what waits for the user first; the board
    with each task's agent, model, status, time, cost and check; its tasks' requests show on its card."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def drawn(condition) -> None:
        for _ in range(150):
            quick.window.grabWindow()
            QApplication.processEvents()
            if condition():
                return
            time.sleep(0.02)
        raise AssertionError("not drawn")

    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        view = app.tasks_view
        bar = quick.window.findChild(QObject, "tasksBar")
        assert not view.shown and not bar.property("visible")  # a chat, not a coordinator
        task = {"criteria": [], "rounds": 0, "verdict": None, "detail": None, "dependencies": [], "waiting": False,
                "cost_usd": 0.0, "steps": 1, "started_at": None, "ended_at": None, "run_id": None}  # fmt: skip
        client.boards["c1"] = [
            task | {"task_id": "a", "agent": "Писатель", "status": "done", "model_id": MODELS[0]["id"],
                    "run_id": "r1", "started_at": "2026-11-05T10:00:00Z", "ended_at": "2026-11-05T10:00:12Z",
                    "cost_usd": 0.0123, "criteria": [{"kind": "command"}], "rounds": 2, "verdict": "ACHIEVED"},
            task | {"task_id": "b", "agent": "Тестер", "status": "running", "waiting": True, "run_id": "r2"},
            task | {"task_id": "c", "agent": "Писатель", "status": "escalated", "run_id": "r3", "rounds": 3,
                    "criteria": [{"kind": "semantic"}], "verdict": "INCOMPLETE",
                    "detail": "not passed after 3 rounds: criterion 1"},
            task | {"task_id": "d", "agent": "Ревьюер", "status": "pending", "dependencies": ["a", "c"]},
        ]  # fmt: skip
        app.on_event({"type": "run.tasks", "payload": {"run_id": "c1"}})
        wait_until(lambda: not app.bg.busy and view.shown)
        assert bar.property("visible") and view.tone == "warn"
        assert view.summary == "Подзадачи: ждёт вас 1 · нужен человек 1 · ждёт очереди 1 · готово 1"
        a, b, c, d = view.rows
        assert (a["statusText"], a["tone"], a["model"], a["time"], a["cost"], a["check"]) == (
            "готово", "ok", MODELS[0]["name"], "12.0 с", "$0.01", "круг 2 из 3 · принято")  # fmt: skip
        assert (b["statusText"], b["tone"], b["check"]) == ("ждёт вас", "warn", "")
        assert (c["statusText"], c["check"], c["detail"]) == ("нужен человек", "круг 3 из 3 · не дотянула",
                                                              "not passed after 3 rounds: criterion 1")  # fmt: skip
        assert (d["statusText"], d["after"], d["time"]) == ("ждёт очереди", "после a, c", "")

        # the task that waits: its request shows here, under the coordinator, named
        asking = {**APPROVAL, "id": "apb", "conversation_id": "r2", "tool": "run_command", "kind": "command",
                  "input": {"command": "pytest -q"}, "preview": {"kind": "command", "text": "pytest -q"}}  # fmt: skip
        app.approvals.answer("ap1", True, "once", "")  # the coordinator's own, older request first
        client.pending = [asking]
        app.on_event({"type": "approval.requested", "payload": {"approval": asking}})
        wait_until(lambda: app.approvals.card.get("id") == "apb")
        assert app.approvals.card["where"].endswith("подзадача [b] «Тестер»")

        QMetaObject.invokeMethod(quick.window.findChild(QObject, "tasksSheet"), "open")
        sheet = quick.window.findChild(QObject, "tasksSheet")
        drawn(lambda: sheet.property("opened") and item_named(sheet.property("contentItem"), "taskRow_d"))
        app.openChat("c0")  # another chat: no board
        wait_until(lambda: not app.bg.busy and not view.shown)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_composers_menus_open_at_their_chips_after_the_window_changed(qapp):
    """2026-11-05 (the user's pictures: «Глубина» and «Права модели» open far from their chips, one past the window's
    edge): a menu's place was a binding worked out when its anchor changed - opened again from the same chip after
    the window changed size, it came where the chip had been. Now each opening works it out; the menu stays in the
    window, and below its chip when there is no room above."""
    from PySide6.QtCore import QObject, QPointF

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def walk(item):
        yield item
        for child in item.childItems():
            yield from walk(child)

    def pump():
        for _ in range(30):
            QApplication.processEvents()
            time.sleep(0.01)

    try:
        window = quick.window
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.newChat()
        pump()

        def chips():
            return {c.property("text"): c for c in walk(window.contentItem())
                    if c.metaObject().className().startswith("Chip") and c.isVisible()}  # fmt: skip

        def opened():
            return [o for o in window.findChildren(QObject) if "Popup" in o.metaObject().className()
                    and o.objectName() != "newProgramPopup" and o.property("opened")]  # fmt: skip

        def open_from(name):
            chip = chips()[name]
            chip.clicked.emit()
            pump()
            (menu,) = opened()
            return chip.mapToScene(QPointF(0, 0)), menu

        window.resize(2560, 900)
        pump()
        for name in [n for n in chips() if n.startswith(("Глубина", "Спрашивать"))]:
            _at, menu = open_from(name)
            menu.close()
            pump()
        window.resize(1280, 1395)  # as the user has it: the chips are elsewhere now
        pump()
        for name in [n for n in chips() if n.startswith(("Глубина", "Спрашивать"))]:
            at, menu = open_from(name)
            x, y, w, h = (menu.property(k) for k in ("x", "y", "width", "height"))
            assert abs(x - (at.x() - 4)) <= 1 and y + h <= at.y() and at.y() - (y + h) <= 10, (name, x, y, at)
            menu.close()
            pump()
        window.resize(820, 600)  # narrow and low: the wide menu stays inside, and opens below when it must
        pump()
        at, menu = open_from(next(n for n in chips() if n.startswith("Спрашивать")))
        x, y, w, h = (menu.property(k) for k in ("x", "y", "width", "height"))
        assert x >= 8 and x + w <= 820 - 8 and y >= 8 and y + h <= 600 - 8, (x, y, w, h)
        menu.close()
        pump()
        window.resize(820, 300)  # the model picker taller than the window: it scrolls inside, no edge passed
        pump()
        _at, menu = open_from(next(iter(chips())))
        x, y, w, h = (menu.property(k) for k in ("x", "y", "width", "height"))
        assert y >= 8 and y + h <= 300 - 8, (y, h)
        assert item_named(menu.property("contentItem"), "menuList") is None  # the list is the content item itself
        assert menu.property("contentItem").property("interactive") is True
        menu.close()
        assert quick.warnings == []
    finally:
        quick.close()


def test_every_popup_and_sheet_stays_inside_a_small_window(qapp):
    """2026-11-05 («находи такие баги в интерфейсе»): every popup, menu and sheet of the window, opened in a window of
    900×600, is inside it once open, and QML says nothing. Found so: the agent's «Пределы» panel went 236 px past
    the top - it is no taller than the room above its card now, and scrolls."""
    from PySide6.QtCore import QMetaObject, QObject, QPointF

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)

    def pump(n):
        for _ in range(n):
            QApplication.processEvents()
            time.sleep(0.01)

    try:
        window = quick.window
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        window.resize(900, 600)
        pump(20)
        popups = [o for o in window.findChildren(QObject)
                  if any(k in o.metaObject().className() for k in ("Popup", "Drawer", "Sheet", "Dialog"))
                  and o.metaObject().indexOfMethod("open()") >= 0]  # fmt: skip
        assert len(popups) > 50
        outside = []
        for popup in popups:
            QMetaObject.invokeMethod(popup, "open")
            deadline = time.monotonic() + 2
            while not popup.property("opened") and time.monotonic() < deadline:
                pump(2)
            pump(3)
            if popup.property("opened"):
                x, y, w, h = (popup.property(k) or 0 for k in ("x", "y", "width", "height"))
                holder = popup.property("parent")
                if holder is not None and hasattr(holder, "mapToScene"):
                    at = holder.mapToScene(QPointF(x, y))
                    x, y = at.x(), at.y()
                if x < -0.5 or y < -0.5 or x + w > 900.5 or y + h > 600.5:
                    outside.append((popup.objectName() or popup.metaObject().className(), round(x), round(y), w, h))
            QMetaObject.invokeMethod(popup, "close")
            pump(8)
        assert outside == []
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_settings_are_a_button_and_a_sheet_for_each_category(qapp):
    """2026-11-05 (the user's word: «сделай кнопку и отдельную окну для каждого категории»): «Настройки» lists the
    categories, each with how it stands now; a category opens a sheet of its own, whose arrow goes back to the list."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.started.update(llama_swap_config="D:/swap/config.yaml", llama_swap_config_found=False,
                              llama_swap_config_source="chosen", llama_swap_script_source="default")  # fmt: skip
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.autostart.get("llama_swap_config"))
        app.openUi("settings")
        menu = quick.window.findChild(QObject, "settingsSheet")
        wait_until(lambda: menu.property("opened"))
        names = ("look", "models", "connections", "agent", "startup", "hub", "transfer", "about")
        found = {name: item_named(menu.property("contentItem"), f"settings_{name}") for name in names}
        assert all(found.values()), found
        assert found["look"].property("summary") == "Русский · Как в системе"
        assert found["models"].property("summary").startswith("Подбор: Баланс")
        assert found["agent"].property("summary").endswith("тихие часы 23:00–08:00")
        startup = found["startup"]
        assert startup.property("warn") and startup.property("summary") == "llama-swap: файла нет — укажите, где он"
        from common import __version__

        assert found["about"].property("summary") == f"AFO {__version__} · обновления"
        found["about"].clicked.emit()  # a category: the list goes, its own sheet comes
        about = quick.window.findChild(QObject, "settingsAbout")
        wait_until(lambda: about.property("opened") and not menu.property("visible"))
        assert item_named(about.property("contentItem"), "releaseCheck") is not None
        back = item_named(about.property("contentItem"), "sheetBack")
        assert back.property("visible")
        back.clicked.emit()
        wait_until(lambda: menu.property("opened") and not about.property("visible"))
        assert not item_named(menu.property("contentItem"), "sheetBack").property("visible")  # the list: no arrow
        found["connections"].clicked.emit()  # this one is the connections' own sheet
        wait_until(lambda: quick.window.findChild(QObject, "connectionsSheet").property("opened"))
        QMetaObject.invokeMethod(quick.window.findChild(QObject, "connectionsSheet"), "close")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_chats_rows_are_used_again_and_forget_their_state(qapp):
    """2026-11-05 («прокрутка иногда лагает»): the chat uses a row that leaves the view again for one that comes in -
    measured at 2560×1400, the longest frames while scrolling a 1000-message chat went from ~25 ms to ~15. A row used
    again forgets what belonged to its old message: the selection, a code block moved sideways, «Скопировано»."""
    from PySide6.QtCore import QMetaObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)

        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)

        def find(prefix):
            return [i for i in walk(quick.window.contentItem()) if i.metaObject().className().startswith(prefix)]

        wait_until(lambda: find("AssistantMessage") and find("CodeBlock"))
        (chat,) = find("ChatView")
        assert chat.property("reuseItems") is True
        user, answer, block = find("UserMessage")[0], find("AssistantMessage")[0], find("CodeBlock")[0]
        texts = [i for i in walk(user) if i.metaObject().className() == "QQuickTextEdit"]
        QMetaObject.invokeMethod(texts[0], "selectAll")
        prose = [i for i in walk(answer) if i.metaObject().className().startswith("Prose")]
        QMetaObject.invokeMethod(prose[0], "selectAll")
        code = next(i for i in walk(block) if i.metaObject().className() == "QQuickTextEdit")
        QMetaObject.invokeMethod(code, "selectAll")
        scroller = next(i for i in walk(block) if i.metaObject().className() == "QQuickFlickable")
        scroller.setProperty("contentX", 40.0)
        assert texts[0].property("selectedText") and prose[0].property("selectedText") and code.property("selectedText")
        QMetaObject.invokeMethod(user, "reused")
        QMetaObject.invokeMethod(answer, "reused")
        assert not texts[0].property("selectedText") and not prose[0].property("selectedText")
        assert not code.property("selectedText") and scroller.property("contentX") == 0
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_busy_window_thread_is_written_down(qapp, caplog):
    """The software scene graph draws on the window's thread, with its Python: a frame waits while that thread is
    busy. The watch writes a stall longer than its limit to the window's log - how long, and where it was."""
    from PySide6.QtCore import QTimer

    from app.ui.hitches import HitchWatch

    watch = HitchWatch(stall_ms=100, quiet_s=0)
    watch.start()
    try:

        def slow_thing():
            time.sleep(0.3)  # the window's thread busy: no frame, no event

        for _ in range(5):
            QApplication.processEvents()
            time.sleep(0.02)
        with caplog.at_level("INFO", logger="app.hitches"):
            QTimer.singleShot(0, slow_thing)
            wait_until(lambda: watch.told, timeout=5)
        (said,) = watch.told
        busy = int(said.split("busy ")[1].split(" ms")[0])
        assert 250 <= busy <= 800 and "slow_thing" in said and said in caplog.text
        for _ in range(10):  # an idle thread: nothing more is written
            QApplication.processEvents()
            time.sleep(0.02)
        assert len(watch.told) == 1
    finally:
        watch.stop()


def test_claude_codes_folder_bar_in_the_window(qapp):
    """31.7: the bar is drawn over the composer when the chat's folder holds files, and the chat leaves it room."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        bar = quick.window.findChild(QObject, "chatFolderBar")
        opened(app)
        wait_until(lambda: not app.bg.busy)
        assert not bar.property("visible")
        made = {"path": "C:/f", "files": [{"path": "a.py", "size": 1}], "count": 1, "truncated": False}
        client.folders = {"c1": made}
        app.on_event(event("chat.done", message_id=2, usage={}, stats={}))
        wait_until(lambda: bar.property("visible"))
        assert quick.window.findChild(QObject, "chatFolderOpen") is not None
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_sandbox_chat_in_the_window(qapp):
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        view = app.sandbox_view
        bar = quick.window.findChild(QObject, "sandboxBar")
        assert not view.active and not bar.property("visible")
        client.sandboxed = [{"project_id": "p1", "name": "LocalChat", "files": [
            {"path": "src/app.py", "status": "changed"}, {"path": "docs.md", "status": "added"}],
            "diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-print('hi')\n+print('привет')\n",
            "exists": True}]  # fmt: skip
        app.setChatMode("sandbox")
        wait_until(lambda: not app.bg.busy and view.count == 2)
        assert view.active and bar.property("visible") and view.summary.startswith("Песочница: изменено 2 файла")
        row = view.projects[0]
        assert [f["label"] for f in row["files"]] == ["src/app.py — изменён", "docs.md — новый"]
        assert all(f["ticked"] for f in row["files"]) and row["applyText"] == "Применить"
        assert [line["mark"] for line in row["lines"]] == ["file", "file", "hunk", "del", "add"]
        # 17.1 (D51): a file unticked stays in the sandbox
        view.tick(row["files"][1]["key"])
        row = view.projects[0]
        assert [f["ticked"] for f in row["files"]] == [True, False]
        assert row["applyText"] == "Применить выбранные (1 из 2)"
        view.tick(row["files"][0]["key"])
        assert view.projects[0]["ticked"] == 0  # nothing ticked: the button is off
        view.apply("p1")
        assert client.sandbox_calls == []
        view.tick(row["files"][0]["key"])
        view.apply("p1")
        wait_until(lambda: not app.bg.busy and view.count == 0)
        assert client.sandbox_calls == [("apply", "c1", "p1", ["src/app.py"])]
        assert app.flashText.startswith("Применено, но с конфликтами: README.md")
        assert app.flashText.endswith("Остальные файлы (1) ждут в песочнице.")
        client.sandboxed = [{"project_id": "p1", "name": "LocalChat", "files": [{"path": "a", "status": "added"}],
                             "diff": "", "exists": True}]  # fmt: skip
        app.on_event({"type": "sandbox.changed", "payload": {"conversation_id": "c1", "project_id": "p1"}})
        wait_until(lambda: not app.bg.busy and view.count == 1)
        assert view.projects[0]["files"][0]["ticked"]  # what was left over comes back ticked
        view.discard("p1")
        assert app.question["title"] == "Отбросить правки в «LocalChat»?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and view.count == 0)
        app.setChatMode("ask")
        wait_until(lambda: not view.active)

        # a coordinator's run (16.2, D50): its tasks left changes in sandboxes of their own
        changed = {"project_id": "p1", "name": "proj", "files": [{"path": "src/app.py", "status": "changed"}],
                   "diff": "-print('hi')\n+print('a')\n", "exists": True}  # fmt: skip
        client.task_sandboxes = [
            {"task_id": "a", "agent": "Правщик", "status": "done", "run_id": "r1", "projects": [changed]},
            {"task_id": "b", "agent": "Правщик", "status": "done", "run_id": "r2",
             "projects": [changed | {"files": [*changed["files"], {"path": "x.py", "status": "added"}]}]}]  # fmt: skip
        app.on_event({"type": "run.finished", "payload": {"run": {"id": "r2", "parent_run": "c1"}}})
        wait_until(lambda: not app.bg.busy and view.taskCount == 3)
        assert view.active and bar.property("visible") and view.count == 0  # not «Песочница» itself
        assert view.summary == "Подзадачи: изменено 3 файла в 2 песочницах — посмотрите и примените или отбросьте"
        assert [r["name"] for r in view.tasks] == ["[a] «Правщик» · proj", "[b] «Правщик» · proj"]
        assert [f["label"] for f in view.tasks[1]["files"]] == ["src/app.py — изменён", "x.py — новый"]
        assert view.taskApplyText == "Применить всё" and view.taskTicked == 3
        view.tick(view.tasks[1]["files"][1]["key"])  # b's x.py stays in its copy
        assert view.taskApplyText == "Применить выбранные (2 из 3)" and view.taskTicked == 2
        view.applyAll()
        wait_until(lambda: not app.bg.busy and view.taskCount == 0)
        ticked = {"a": {"p1": ["src/app.py"]}, "b": {"p1": ["src/app.py"]}}
        assert client.sandbox_calls[-1] == ("apply all", "c1", ticked)
        assert not view.active
        assert app.flashText == "Применено к проекту: 3 файлов Не применены (подзадача не закончилась): c"
        client.task_sandboxes = [{"task_id": "a", "agent": "Правщик", "status": "done", "run_id": "r1",
                                  "projects": [changed]}]  # fmt: skip
        app.on_event({"type": "sandbox.changed", "payload": {"conversation_id": "c1"}})
        wait_until(lambda: not app.bg.busy and view.taskCount == 1)
        assert view.taskTicked == 1
        view.applyAll()
        wait_until(lambda: not app.bg.busy and view.taskCount == 0)
        assert client.sandbox_calls[-1] == ("apply all", "c1")  # all ticked: no list - every file
        client.task_sandboxes = [{"task_id": "a", "agent": "Правщик", "status": "done", "run_id": "r1",
                                  "projects": [changed]}]  # fmt: skip
        app.on_event({"type": "sandbox.changed", "payload": {"conversation_id": "c1"}})
        wait_until(lambda: not app.bg.busy and view.taskCount == 1)
        view.discardAll()
        assert app.question["title"] == "Отбросить правки всех подзадач?"
        app.answer(True)
        wait_until(lambda: not app.bg.busy and view.taskCount == 0)
        assert client.sandbox_calls[-1] == ("discard all", "c1")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_installing_a_local_model_from_the_window(qapp):
    from PySide6.QtCore import QObject

    from app.ui.install_view import gb, progress_text, trial_text
    from app.ui.main import QuickApp

    assert gb(8_030_000_000) == "8,0 ГБ" and gb(14.2, of_bytes=False) == "14,2 ГБ"
    assert progress_text({"status": "downloading", "done_bytes": 2_000_000_000, "total_bytes": 8_000_000_000,
                          "speed": 20_000_000}) == "2,0 ГБ из 8,0 ГБ · 20,0 МБ/с · ещё ≈ 5 мин"  # fmt: skip
    assert trial_text({"status": "done", "trial": {"seconds": 4.6, "tok_s": 85.4, "where": "gpu", "tries": [{}, {}]}}
                      ) == "загрузилась и ответила за 4,6 с · 85 ток/с · целиком в видеопамяти · с 2-й попытки — " \
                           "настройки уменьшены"  # fmt: skip
    assert trial_text({"status": "done", "trial": {"seconds": 31.0, "size": 512, "where": "cpu"}}) == (  # 29.4в
        "загрузилась и ответила за 31,0 с · на процессоре — видеокарта здесь не нарисовала")  # fmt: skip
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.install_view
        sheet = quick.window.findChild(QObject, "installSheet")
        app.openUi("install-models")
        wait_until(lambda: sheet.property("opened") and not app.bg.busy and view.catalog)
        assert view.systemText.startswith("RTX 5080: для новой модели ≈ 14,2 ГБ видеопамяти · ОЗУ свободно ≈ 42,0 ГБ")
        rows = view.catalog
        assert rows[0]["offer"] == "Q8_0 · 8,0 ГБ" and rows[0]["tone"] == "ok" and rows[0]["canInstall"]
        assert rows[1]["tone"] == "error" and "63,4 ГБ" in rows[1]["words"] and not rows[1]["canInstall"]
        view.setPurpose("code")
        assert [r["id"] for r in view.catalog] == ["gpt-oss-120b"]
        assert [p["name"] for p in view.purposes][-2:] == ["Понимать картинки", "Рисовать"]
        view.setPurpose("image")  # picture models (29.3): the hub's own list, no variants to open
        wait_until(lambda: not app.bg.busy and view.catalog)
        light, best = view.catalog
        assert (light["name"], light["installed"], light["canInstall"], light["variants"]) == ("SDXS 512", True, False,
                                                                                                 False)  # fmt: skip
        assert (best["offer"], best["tone"], best["words"], best["canInstall"]) == (
            "6,2 ГБ", "warn", "медленно — минуты на картинку", True)  # fmt: skip
        view.installRecommended(best["repo"])
        wait_until(lambda: not app.bg.busy and client.install_calls[-1][0] == "pictures")
        assert client.install_calls[-1] == ("pictures", {"id": "z-image-turbo"})
        assert view.catalog[1]["job"]["line"] == "в очереди"  # its row shows the install
        client.install_jobs_list = []
        view._jobs = []
        view.setPurpose("")
        places = view.drives  # LocalChat's own `model` folder first, and chosen (2026-10-28, the user's word)
        assert [d["id"] for d in places] == ["home", "D", "F"] and places[0]["selected"] and places[2]["slow"]
        assert places[0]["name"] == "Папка AFO (D: NVMe) · свободно 456,5 ГБ"

        assert view.status["name"] == "" and not quick.window.findChild(QObject, "statusInstall").property("visible")
        view.installRecommended("ggml-org/gemma-4-E4B-it-GGUF")
        wait_until(lambda: not app.bg.busy and view.jobs)
        assert client.install_calls[-1] == ("install", {"repo": "ggml-org/gemma-4-E4B-it-GGUF", "quant": "Q8_0"})
        assert view.tab == "catalog"  # it stays: the row itself shows the install now (2026-10-28, the user's word)
        assert view.activeCount == 1 and view.jobs[0]["statusWords"] == "в очереди"
        card = view.catalog[0]["job"]  # «Установить» gave way to «Пауза» and ✕
        assert (card["primary"], card["primaryLabel"], card["dismiss"], card["line"]) == ("pause", "Пауза", "cancel",
                                                                                           "в очереди")  # fmt: skip
        half = {"id": "j1", "done_bytes": 4_295_000_000, "total_bytes": 8_590_000_000, "speed": 21_700_000}
        app.on_event({"type": "install.progress", "payload": half})
        row = view.jobs[0]
        assert row["percent"] == 50.0 and row["progress"].startswith("4,3 ГБ из 8,6 ГБ · 21,7 МБ/с")
        assert [a["id"] for a in row["actions"]] == ["pause", "cancel"]
        assert view.catalog[0]["job"]["line"] == "50 % · 4,3 ГБ из 8,6 ГБ · 21,7 МБ/с · ещё ≈ 3 мин"
        bar = view.status  # the status bar at the bottom of the window
        assert (bar["name"], bar["text"], bar["percent"]) == ("Gemma 4 E4B Q8_0", "50 % · 21,7 МБ/с · ещё ≈ 3 мин", 0.5)
        assert bar["tip"].endswith("Нажмите — «Установки»")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "statusInstall").property("visible")
        view.act("j1", "pause")
        wait_until(lambda: not app.bg.busy and view.jobs[0]["status"] == "paused")
        assert [a["label"] for a in view.jobs[0]["actions"]] == ["Продолжить", "Отменить"]
        paused = view.catalog[0]["job"]
        assert (paused["primary"], paused["primaryLabel"], paused["line"]) == (
            "resume",
            "Продолжить",
            "на паузе · 50 % · 4,3 ГБ из 8,6 ГБ",
        )
        assert view.status["name"] == ""  # nothing goes on: the status bar says nothing
        sheet.close()
        view.setTab("catalog")
        view.showJobs()  # the status bar clicked: the sheet on «Установки»
        wait_until(lambda: sheet.property("opened"))
        assert view.tab == "jobs" and view.repoName == ""
        done = {**client.install_jobs_list[0], "status": "done", "done_bytes": 8_590_000_000, "percent": 100.0,
                "trial": {"seconds": 4.6, "tok_s": 480.0, "where": "gpu", "tries": [{"ok": True}]}}  # fmt: skip
        client.install_jobs_list = [done]
        app.on_event({"type": "install.changed", "payload": {"job": done}})
        wait_until(lambda: not app.bg.busy)
        assert app.flashText.startswith("Модель «Gemma 4 E4B Q8_0» установлена: загрузилась и ответила за 4,6 с")
        assert view.activeCount == 0 and view.jobs[0]["trial"].endswith("целиком в видеопамяти")
        view.act("j1", "remove")
        assert app.question["title"] == "Удалить модель «Gemma 4 E4B Q8_0»?" and "8,6 ГБ" in app.question["text"]
        app.answer(True)
        wait_until(lambda: not app.bg.busy and not view.jobs)
        assert client.install_calls[-1] == ("remove", "gemma-4-e4b-it-q8_0")

        view.setTab("search")
        view.search("tiny")
        wait_until(lambda: not app.bg.busy and view.found)
        assert client.install_calls[-1] == ("search", {"q": "tiny", "task": "", "size": "", "sort": "downloads",
                                                       "plain": False})  # fmt: skip
        assert view.found[0]["badges"] == ["снятые ограничения"] and view.found[0]["note"].startswith("↓ 1,2 млн")
        assert "27,3 млрд параметров" in view.found[0]["note"]
        # the filters (32.6): a pick searches again with the words in the field, sent or not
        assert [t["name"] for t in view.searchTasks] == ["Все", "Разговоры", "Код", "Понимать картинки"]
        assert [s["name"] for s in view.searchSizes][:3] == ["Любой", "Поместится в видеопамять (до 22 млрд)",
                                                             "до 4 млрд"]  # fmt: skip
        assert view.searchSort == "downloads" and len(view.searchSorts) == 4
        view.setQuery("tiny coder")
        view.setSearchTask("code")
        wait_until(lambda: not app.bg.busy and client.install_calls[-1][1]["task"] == "code")
        view.setSearchSize("fits")
        view.setSearchSort("likes")
        view.setSearchPlain(True)
        wait_until(lambda: not app.bg.busy and client.install_calls[-1][1]["plain"])
        assert client.install_calls[-1] == ("search", {"q": "tiny coder", "task": "code", "size": "fits",
                                                       "sort": "likes", "plain": True})  # fmt: skip
        assert (view.searchTask, view.searchSize, view.searchSort, view.searchPlain) == ("code", "fits", "likes", True)
        for _ in range(10):
            QApplication.processEvents()
        for name in ("installSearchTask", "installSearchSize", "installSearchSort", "installSearchPlain"):
            assert quick.window.findChild(QObject, name).property("visible"), name
        assert not view.nothingFound
        view.search("nothing")
        wait_until(lambda: not app.bg.busy and not view.searching)
        assert view.found == [] and view.nothingFound
        view.search("")  # no words: the most popular, by the filters
        wait_until(lambda: not app.bg.busy and view.found)
        assert client.install_calls[-1][1]["q"] == "" and not view.nothingFound
        view.openRepo("ggml-org/gemma-4-E4B-it-GGUF")
        wait_until(lambda: not app.bg.busy and view.variants)
        assert quick.window.findChild(QObject, "installRepo").property("visible")
        assert [(v["quant"], v["size"], v["recommended"]) for v in view.variants] == [
            ("Q4_0", "4,6 ГБ", False), ("Q8_0", "8,0 ГБ", True)]  # fmt: skip
        assert view.repo["projectorText"] == "Понимать картинки (+0,6 ГБ)" and "7,6 млрд" in view.repo["notes"]
        assert view.target == "D:\\AI model\\LocalChat\\model\\ggml-org\\gemma-4-E4B-it-GGUF"
        assert quick.window.findChild(QObject, "installBrowse").property("visible")
        view.setFolder("file:///E:/Модели")  # «Обзор…» gives a file:// address
        assert view.folder == "E:/Модели" and view.target.startswith("E:/Модели\\ggml-org")
        view.setDrive("home")
        assert view.folder == "" and view.target.startswith("D:\\AI model\\LocalChat\\model")
        view.setProjector(False)
        view.setDrive("F")
        view.install("Q4_0")
        wait_until(lambda: not app.bg.busy and view.variants[0]["job"])
        assert view.variants[0]["job"]["primary"] == "pause" and view.variants[1]["job"] == {}  # only its own row
        assert client.install_calls[-1] == ("install", {"repo": "ggml-org/gemma-4-E4B-it-GGUF", "quant": "Q4_0",
                                                        "projector": False, "drive": "F"})  # fmt: skip
        view.closeRepo()
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_choosing_where_llama_swap_is(qapp):
    """10.3: «Настройки → Запуск» shows llama-swap's config and start and takes the files chosen."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.started.update(llama_swap_config="D:/swap/config.yaml", llama_swap_config_found=True,
                              llama_swap_config_source="default", llama_swap_script_source="default")  # fmt: skip
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.autostart.get("llama_swap_config"))
        app.openUi("settings")
        wait_until(lambda: quick.window.findChild(QObject, "settingsSheet").property("opened"))
        app.chooseLlamaSwap("llama_swap_config", "file:///E:/models/swap.yaml")
        wait_until(lambda: not app.bg.busy and app.autostart["llama_swap_config"] == "E:/models/swap.yaml")
        assert app.flashText == "Конфиг llama-swap: E:/models/swap.yaml"
        app.chooseLlamaSwap("llama_swap_start", "")
        wait_until(lambda: not app.bg.busy and app.autostart.get("llama_swap_start") == "")
        app.chooseLlamaSwap("login", "x")  # only the two paths
        assert "login" not in [k for k, v in app.autostart.items() if v == "x"]
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_first_run_wizard(qapp):
    """10.2: a computer without an engine opens the wizard by itself, once; the engine's progress; the first models."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp
    from app.ui.setup_view import DISMISSED

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.setup = {"needed": True, "answering": False, "folder": "C:/Users/u/AppData/Local/LocalChat/engines",
                        "engine": {"config_found": False}, "job": {"status": "idle"},
                        "cards": [{"name": "Radeon RX 9070", "vendor": "amd", "vram_gib": 16.0},
                                  {"name": "Intel(R) Graphics", "vendor": "intel", "vram_gib": 2.0}],
                        "build": {"build": "vulkan", "card": "Radeon RX 9070",
                                  "why": "видеокарта AMD или Intel: сборка с Vulkan"},
                        "builds": {"cuda-13": "NVIDIA, CUDA 13", "vulkan": "Vulkan (AMD, Intel, NVIDIA)",
                                   "cpu": "только процессор"},
                        "memory": {"ram_total_gib": 32.0, "commit_limit_gib": 36.0},
                        "page_file": {"gib": 4.0, "advice": "Файл подкачки — 4 ГБ."},
                        "pictures": {"build": "vulkan", "video": "slow",
                                     "why": "Radeon RX 9070: картинки 1024 × 1024 за секунды; видео здесь — только "
                                            "попробовать: десятки минут на ролик"}}  # fmt: skip
        app.attach(client, start_events=False)
        sheet = quick.window.findChild(QObject, "wizardSheet")
        wait_until(lambda: not app.bg.busy and sheet.property("opened"))  # opened by itself
        view = app.setup_view
        assert view.cards == ["Radeon RX 9070 — 16 ГБ видеопамяти", "Intel(R) Graphics — 2 ГБ видеопамяти"]
        assert view.build == "vulkan" and view.memoryText == "ОЗУ 32 ГБ · файл подкачки ≈ 4 ГБ"
        assert view.engineText.startswith("Движка для локальных моделей ещё нет") and view.pageAdvice
        assert view.picturesText == ("Картинки и видео: Radeon RX 9070: картинки 1024 × 1024 за секунды; видео здесь "
                                     "— только попробовать: десятки минут на ролик")  # fmt: skip
        view.setBuild("cpu")
        view.installEngine()
        wait_until(lambda: not app.bg.busy and view.jobStatus == "downloading")
        assert client.install_calls[-1] == ("engine", "cpu")
        job = {"status": "downloading", "build": "cpu", "done": 12_000_000, "total": 48_000_000,
               "step": "llama.cpp b200 (только процессор) и llama-swap v300"}  # fmt: skip
        app.on_event({"type": "setup.progress", "payload": {"job": job}})
        assert view.jobPercent == 25.0 and view.jobText == ("Скачиваю llama.cpp b200 (только процессор) и llama-swap "
                                                            "v300 — 12 МБ из 48 МБ")  # fmt: skip
        client.setup = {**client.setup, "needed": False, "answering": True, "job": {"status": "done"}}
        app.on_event({"type": "setup.changed", "payload": {"job": {"status": "done"}}})
        wait_until(lambda: not app.bg.busy and view.models and view.models[0]["name"])
        rows = view.models
        assert [(r["title"], r["offer"], r["canInstall"], r["installed"]) for r in rows] == [
            ("Разговоры", "Q8_0 · 8,0 ГБ", True, False), ("Код", "Q8_0 · 8,1 ГБ", True, True),
            ("Понимать картинки", "", False, False), ("Рисовать", "4,9 ГБ", True, False)]  # fmt: skip
        view.installModel("image")  # a picture model (29.3): its own install, the engine with it
        wait_until(lambda: not app.bg.busy and client.install_calls[-1][0] == "pictures")
        assert client.install_calls[-1] == ("pictures", {"id": "flux2-klein-4b"})
        view.installModel("chat")
        wait_until(lambda: not app.bg.busy and client.install_calls[-1][0] == "install")
        assert client.install_calls[-1][1]["repo"] == "ggml-org/gemma-4-E4B-it-GGUF"
        wait_until(lambda: not app.bg.busy and view.updateAvailable)
        assert view.updateText.startswith("llama.cpp b11899 — есть новее: b11946")
        view.updateEngine()
        wait_until(lambda: not app.bg.busy and client.install_calls[-1] == ("update",))
        view.dismiss()
        assert app.settings.value(DISMISSED, False, bool)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_wizard_waits_while_a_list_is_open(qapp):
    """2026-10-28: the wizard opening by itself does not take the keys from an open list (a comment being typed in an
    approval card): it opens once the list closes. Asked for from Settings, it opens at once."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        listed = quick.window.findChild(QObject, "approvalsList")
        sheet = quick.window.findChild(QObject, "wizardSheet")
        app.openUi("approvals")
        wait_until(lambda: listed.property("opened"))
        listed.property("contentItem").forceActiveFocus()  # the user types in it (a comment in a card)
        app.setup_view.openRequested.emit()  # the hub said: this computer needs the wizard
        for _ in range(20):
            QApplication.processEvents()
        assert not sheet.property("visible") and listed.property("opened")
        QMetaObject.invokeMethod(listed, "close")
        wait_until(lambda: sheet.property("opened"))
        QMetaObject.invokeMethod(sheet, "close")
        wait_until(lambda: not sheet.property("visible"))
        app.openUi("approvals")
        wait_until(lambda: listed.property("opened"))
        app.openUi("setup")  # from Settings or the command palette: the user asked
        wait_until(lambda: sheet.property("opened"))
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_checking_the_system_and_collecting_the_logs(qapp):
    """10.6: «Проверить систему» runs when its sheet opens; «Собрать журналы» makes the report."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openUi("check-system")
        sheet = quick.window.findChild(QObject, "checkSheet")
        wait_until(lambda: sheet.property("opened") and not app.bg.busy and app.check_view.lines)
        view = app.check_view
        assert view.summary == "Стоит взглянуть: 1" and [r["tone"] for r in view.lines] == ["ok", "warn"]
        view.collect()
        wait_until(lambda: not app.bg.busy and view.report)
        assert app.flashText == "Журналы собраны: C:/data/reports/afo-report.zip"
        assert ("report",) in client.install_calls
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_level_of_a_task_and_the_cards_words(qapp):
    """10.x: a new task starts «Авто» unless the person chose otherwise; a running one changes its level; the card for
    acting in a program says what «for the task» covers."""
    from app.ui.approvals import approval_rows
    from app.ui.autonomy import LEVEL_KEY
    from app.ui.main import QuickApp

    rows = approval_rows([{"id": "a1", "tool": "screen_act", "kind": "command", "input": {"program": "Telegram"},
                           "preview": {"kind": "text", "text": "нажать"}}], projects={}, chats={})  # fmt: skip
    assert rows[0]["chatScope"] == "«Telegram» до конца задачи"
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.autonomy_view
        app.settings.remove(LEVEL_KEY)
        view.newTask()
        assert view.draft["autonomy"] == "free"  # «Авто» at first
        view.setField("autonomy", "plan")
        view.newTask()
        assert view.draft["autonomy"] == "plan"  # the person's last choice
        app.settings.remove(LEVEL_KEY)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_agent_input_as_a_chat(qapp):
    """2026-10-28: the Agent mode works as a chat - the goal is typed in the input, its mode / model / projects /
    «when» / limits are chips with explanations; under a running task the same input answers the agent's question or
    gives it another limit, and the mode chip changes the running task's mode."""
    from PySide6.QtCore import QObject

    from app.ui.autonomy import AUTONOMY, LEVEL_KEY
    from app.ui.main import QuickApp

    assert [name for _id, name, _hint in AUTONOMY] == ["Полный", "Авто", "Сначала план", "Каждый шаг"]
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.run_list = [dict(AUTO_RUN), dict(RUN)]
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.autonomy_view.tasks)
        view = app.autonomy_view
        app.settings.remove(LEVEL_KEY)
        app.setMode("agent")
        view.newTask()
        for _ in range(10):
            QApplication.processEvents()
        assert not view.running and view.modeName == "Авто" and view.modeHint.startswith("Работает сам")
        modes = view.modes
        assert modes[0]["section"] and [m["name"] for m in modes[1:]] == ["Полный", "Авто", "Сначала план",
                                                                           "Каждый шаг"]  # fmt: skip
        assert modes[2]["selected"] and all(m["note"] for m in modes[1:])  # each mode explained; «Авто» at first
        # «Полный» asks nothing (33.4, D56): a bolt, and what it never does said in its hint
        assert modes[1]["icon"] == "bolt" and {m["icon"] for m in modes[2:]} == {"shield"}
        assert modes[1]["note"].startswith("Ничего не спрашивает") and "удаляющее и необратимое" in modes[1]["note"]
        assert "о новой программе спросит один раз" in modes[2]["note"]
        assert quick.window.findChild(QObject, "agentMode").property("iconName") == "shield"
        view.chooseMode("full")
        for _ in range(10):
            QApplication.processEvents()
        assert view.modeName == "Полный" and quick.window.findChild(QObject, "agentMode").property("iconName") == "bolt"
        view.chooseMode("free")
        limits = view.limitFields
        assert [f["id"] for f in limits] == ["done_when", "max_steps", "max_minutes", "budget_usd", "max_replans"]
        assert all(f["hint"] for f in limits)
        assert view.limitsText == "100 шагов · 60 мин · $1" and view.projectsText == "Без проектов"
        assert view.placeholder.startswith("Что сделать агенту?")
        assert quick.window.findChild(QObject, "agentMode").property("text") == "Авто"
        assert quick.window.findChild(QObject, "agentModeHint").property("text").startswith("«Авто» — Работает сам")
        view.chooseMode("each_step")  # no task running: the next task's mode, remembered
        assert view.draft["autonomy"] == "each_step" and app.settings.value(LEVEL_KEY) == "each_step"
        view.setField("model_id", "")
        assert view.submit("Посчитать строки") is False and view.error.startswith("Выберите модель")  # stays
        view.setField("model_id", app.modelId)
        assert view.submit("   ") is False
        assert view.submit(" Посчитать строки в README ") is True
        wait_until(lambda: not app.bg.busy and client.auto_calls)
        started = client.auto_calls[0]
        assert started[0] == "start" and started[1]["goal"] == "Посчитать строки в README"
        assert started[1]["autonomy"] == "each_step"

        view.select("auto1")  # running, with a question waiting
        wait_until(lambda: not app.bg.busy and view.task.get("planVersion") == 2)
        assert view.running and view.modeName == "Сначала план"  # the task's own mode
        assert view.placeholder.startswith("Ответ на вопрос агента")
        assert view.thread == []  # the waiting question is the card under the thread
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "agentQuestion").property("visible")
        assert quick.window.findChild(QObject, "agentStop").property("visible")
        assert not quick.window.findChild(QObject, "agentModel").property("visible")  # a new task's chips hide
        assert view.submit("нет") is True
        wait_until(lambda: not app.bg.busy and app.flashText == "Ответ передан агенту")
        assert view.submit("И не трогай другие файлы") is True
        wait_until(lambda: not app.bg.busy and view.thread)
        assert [(r["who"], r["text"]) for r in view.thread] == [("you", "И не трогай другие файлы")]
        assert app.flashText == "Агент прочтёт это перед следующим шагом"
        view.chooseMode("free")  # a running task: its mode changes on the way
        wait_until(lambda: not app.bg.busy and ("autonomy", "auto1", "free") in client.auto_calls)
        assert ("message", "auto1", "нет") in client.auto_calls
        assert app.settings.value(LEVEL_KEY) == "each_step"  # the running task's mode is not the next task's
        app.settings.remove(LEVEL_KEY)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_refusal_switch_for_a_model_and_a_new_task(qapp):
    """2026-10-28 (the user's word): a Strata model with a refusal vector gets «Снятие отказов» in its settings, and a
    new agent task gets its own switch - starting from how the model is set; a model without one shows none."""
    from PySide6.QtCore import QObject

    from app.ui import model_setup as ms
    from app.ui.main import QuickApp

    draft = ms.draft_of({"params": {"refusal_vector": False}})
    assert draft["refusal_vector"] == "off"
    assert ms.settings_of(draft, ["refusal_vector"]) == {"params": {"refusal_vector": False}}
    assert ms.settings_of({**draft, "refusal_vector": ""}, ["refusal_vector"]) == {}  # as the model is
    assert ms.settings_of(draft, ["temperature"]) == {}  # a model without the vector: not sent
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        vectored = {"id": "llama-swap/flash", "model_ref": "flash", "name": "Flash", "locality": "local", "prices": {},
                    "accepts": ["refusal_vector", "temperature"], "refusal_vector": False}  # fmt: skip
        client.models = lambda: [*MODELS, vectored]
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and "llama-swap/flash" in app.models)
        view = app.autonomy_view
        app.setMode("agent")
        view.newTask()
        view.setField("model_id", "llama-swap/coder")
        assert not view.refusalShown  # its abliteration is in its weights, or none: nothing to switch
        view.setField("model_id", "llama-swap/flash")
        for _ in range(10):
            QApplication.processEvents()
        chip = quick.window.findChild(QObject, "agentRefusal")
        assert view.refusalShown and chip.property("visible") and chip.property("text") == "Снятие отказов: выкл"
        items = view.refusalItems
        assert items[1]["name"] == "Как в настройках модели" and items[1]["note"] == "Сейчас там: выключено."
        assert (
            items[1]["selected"] and [i["id"] for i in items[2:]] == ["on", "off"] and all(i["note"] for i in items[1:])
        )
        view.chooseRefusal("on")
        assert view.refusalName == "Снятие отказов: вкл" and view.refusalItems[2]["selected"]
        assert view.submit("Ответь одним словом") is True
        wait_until(lambda: not app.bg.busy and client.auto_calls)
        assert (
            client.auto_calls[0][1]["refusal_vector"] is True
            and client.auto_calls[0][1]["model_id"] == "llama-swap/flash"
        )
        view.newTask()
        view.setField("model_id", "llama-swap/flash")
        view.chooseRefusal("model")
        from app.ui.autonomy import goal_body

        assert goal_body({**view._draft, "goal": "x"})["refusal_vector"] is None  # as the model is set
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_refusal_switch_in_a_chat(qapp):
    """2026-10-28 (the user's word): the chat input has «Снятие отказов» for a model with Strata's vector - each chat
    its own, starting from the model's setting; a new chat takes it with its first message, an open one at once."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        vectored = {"id": "llama-swap/flash", "model_ref": "flash", "name": "Flash", "locality": "local", "prices": {},
                    "accepts": ["refusal_vector", "temperature"], "refusal_vector": False}  # fmt: skip
        client.models = lambda: [*MODELS, vectored]
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and "llama-swap/flash" in app.models)
        app.setMode("chats")
        app.newChat()
        app.chooseModel("anthropic/claude-opus-5-5")
        assert not app.chatRefusalShown
        app.chooseModel("llama-swap/flash")
        for _ in range(10):
            QApplication.processEvents()
        chip = quick.window.findChild(QObject, "chatRefusal")
        assert app.chatRefusalShown and chip.property("visible") and chip.property("text") == "Снятие отказов: выкл"
        items = app.chatRefusalItems
        assert items[0]["section"].endswith("для этого чата") and items[1]["note"] == "Сейчас там: выключено."
        app.setChatRefusal("on")  # a chat not made yet: with its first message
        assert app.chatRefusalName == "Снятие отказов: вкл" and not [e for e in client.log if e[0] == "context"]
        app.send("привет")
        wait_until(lambda: not app.bg.busy and client.sent)
        assert ("context", "c2", {"refusal_vector": "on"}) in client.log and app.current == "c2"
        app.setChatRefusal("model")  # an open chat: saved at once
        wait_until(lambda: not app.bg.busy and ("context", "c2", {"refusal_vector": ""}) in client.log)
        assert app.chatRefusalName == "Снятие отказов: выкл"
        app.newChat()  # a new chat starts from the model's setting again
        assert app.chatRefusalName == "Снятие отказов: выкл" and app.chatRefusalItems[1]["selected"]
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_drawing_a_picture_in_the_window(qapp, tmp_path):
    """29.5 (D55): «Нарисовать» - the description from the chat's input, the shape, the picture as it is drawn,
    «Стоп», the result, «Сохранить как…», «Качественнее» and «Ещё вариант»."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.visual_view
        sheet = quick.window.findChild(QObject, "visualSheet")
        assert quick.window.findChild(QObject, "drawChip") is not None  # the chip in the chat's input
        view.open("рыжая лиса в снегу")
        app.openUi("draw")
        wait_until(lambda: sheet.property("opened") and not app.bg.busy and view.hasModels)
        assert view.prompt == "рыжая лиса в снегу" and view.choice
        assert view.modelText == "FLUX.2 [klein] 4B · 1024 × 1024"  # «Быстрее»: the hub's default
        view.setShape("wide")
        assert view.modelText.endswith("1344 × 768")
        view.draw()
        wait_until(lambda: not app.bg.busy and client.visual_calls)
        assert client.visual_calls[-1] == ("draw", {"prompt": "рыжая лиса в снегу", "model": "flux2-klein-4b",
                                                    "width": 1344, "height": 768, "seed": -1})  # fmt: skip
        assert view.busy and view.pictureSource == ""
        job = {**client.visual_jobs_list[0], "status": "running"}
        app.on_event({"type": "visual.changed", "payload": {"job": job}})
        preview = base64.b64encode(PICTURE).decode()
        app.on_event({"type": "visual.progress", "payload": {"id": job["id"], "step": 2, "steps": 4,
                                                             "preview": preview}})  # fmt: skip
        assert view.statusText == "рисую: шаг 2 из 4" and view.percent == 50.0
        assert view.pictureSource.startswith("image://pictures/preview/")
        view.stop()
        wait_until(lambda: not app.bg.busy and client.visual_calls[-1][0] == "cancel")
        assert client.visual_calls[-1] == ("cancel", job["id"])
        done = {**job, "status": "done", "seconds": 4.5, "file": f"2026-11/{job['id']}.png"}
        app.on_event({"type": "visual.changed", "payload": {"job": done}})
        wait_until(lambda: not app.bg.busy and view.done)
        assert view.pictureSource == f"image://pictures/job/{job['id']}" and view.statusText == "готово за 4,5 с"
        assert [r["id"] for r in view.recent] == [job["id"]] and view.recent[0]["chosen"]
        assert view.provider.requestImage(f"job/{job['id']}", QSize(), QSize()).width() == 32
        view.saveAs(str(tmp_path / "fox"))
        assert (tmp_path / "fox.png").read_bytes() == PICTURE
        view.setQuality("quality")
        assert view.modelText.startswith("Z-Image-Turbo")
        view.again()  # the same description, a new seed, the quality model
        wait_until(lambda: not app.bg.busy and len(client.visual_calls) == 3)
        assert client.visual_calls[-1][1]["model"] == "z-image-turbo" and client.visual_calls[-1][1]["seed"] == -1
        failed = {**client.visual_jobs_list[0], "status": "failed", "error": "картинка вышла пустой"}
        app.on_event({"type": "visual.changed", "payload": {"job": failed}})
        assert view.statusText == "не вышло: картинка вышла пустой" and not view.busy

        # 29.4б: a picture that came out a rung lower says how; the rung remembered here shows before it is drawn
        tries = [{"model_id": "z-image-turbo", "width": 1344, "height": 768, "rung": "asked", "ok": False},
                 {"model_id": "z-image-turbo", "width": 672, "height": 384, "rung": "smaller", "ok": None}]  # fmt: skip
        lower = {**failed, "status": "running", "width": 672, "height": 384, "tries": tries[:1]}
        app.on_event({"type": "visual.changed", "payload": {"job": lower}})
        assert view.statusText == "не вышло — пробую меньшим размером, 672 × 384"
        app.on_event({"type": "visual.progress", "payload": {"id": lower["id"], "step": 1, "steps": 8}})
        assert view.statusText == "рисую: шаг 1 из 8 — меньшим размером, 672 × 384"
        app.on_event({"type": "visual.changed", "payload": {"job": {**lower, "status": "done", "seconds": 12.0,
                                                                    "tries": tries, "file": "x.png"}}})  # fmt: skip
        wait_until(lambda: not app.bg.busy and view.done)
        assert view.statusText == "готово за 12 с — меньшим размером, 672 × 384"
        flux = {**tries[1], "model_id": "flux2-klein-4b", "rung": "lighter"}
        lighter = {**lower, "status": "done", "seconds": 12.0, "model_id": "flux2-klein-4b",
                   "tries": [tries[0], {**tries[1], "ok": False}, flux]}  # fmt: skip
        app.on_event({"type": "visual.changed", "payload": {"job": lighter}})
        assert view.statusText == "готово за 12 с — моделью FLUX.2 [klein] 4B"
        client.visual_rungs = {"z-image-turbo": {"instead": "flux2-klein-4b"}, "flux2-klein-4b": {"pixels": 512 * 512}}
        view.load()
        wait_until(lambda: not app.bg.busy and view.modelText.startswith("FLUX"))
        assert view.modelText == "FLUX.2 [klein] 4B · 672 × 384 — Z-Image-Turbo здесь не выходит"
        view.again()
        wait_until(lambda: not app.bg.busy and len(client.visual_calls) == 4)
        assert client.visual_calls[-1][1] | {"seed": 0} == {"prompt": "рыжая лиса в снегу", "model": "flux2-klein-4b",
                                                           "width": 672, "height": 384, "seed": 0}  # fmt: skip
        view.setQuality("fast")
        assert view.modelText == "FLUX.2 [klein] 4B · 672 × 384 — крупнее здесь не выходит"
        # 29.4в: the card draws nothing here - the processor does, and the sheet says so before
        client.visual_rungs = {"flux2-klein-4b": {"instead": "flux2-klein-4b-cpu", "cpu_id": "flux2-klein-4b-cpu"}}
        view.load()
        wait_until(lambda: not app.bg.busy and "процессор" in view.modelText)
        assert view.modelText == "FLUX.2 [klein] 4B · 672 × 384 — рисует процессор: видеокарта здесь не вышла"
        cpu_try = {**tries[1], "model_id": "flux2-klein-4b-cpu", "rung": "cpu"}
        on_cpu = {**client.visual_jobs_list[0], "status": "done", "seconds": 12.0, "model_id": "flux2-klein-4b-cpu",
                  "tries": [{**tries[0], "model_id": "flux2-klein-4b"}, cpu_try]}  # fmt: skip
        app.on_event({"type": "visual.changed", "payload": {"job": on_cpu}})
        assert view.statusText == "готово за 12 с — процессором"
        view.again()
        wait_until(lambda: not app.bg.busy and len(client.visual_calls) == 5)
        assert client.visual_calls[-1][1] | {"seed": 0} == {"prompt": "рыжая лиса в снегу",
                                                           "model": "flux2-klein-4b-cpu", "width": 672, "height": 384,
                                                           "seed": 0}  # fmt: skip

        # 29.6б «По картинке»: the picture shown, a file, a chat's picture - with how much to change it
        view.setQuality("quality")
        client.visual_rungs = {}
        view.load()
        wait_until(lambda: not app.bg.busy and view.modelText.startswith("Z-Image"))
        view._job = {**client.visual_jobs_list[0], "status": "done", "id": "v1"}
        view.provider.pictures["v1"] = PICTURE
        view.fromThis()
        assert view.hasSource and view.sourceSource.startswith("image://pictures/source/")
        assert view.strength == "some" and [s["id"] for s in view.strengths] == ["little", "some", "much"]
        assert view.modelText == "Z-Image-Turbo · по картинке"
        view.setStrength("much")
        view.draw()
        wait_until(lambda: not app.bg.busy and client.visual_calls[-1][0] == "draw")
        spec = client.visual_calls[-1][1]
        assert (spec["from_job"], spec["strength"], spec["model"]) == ("v1", 0.85, "z-image-turbo")
        assert "width" not in spec and "height" not in spec  # the source's shape: the hub sizes it
        client.visual_jobs_list[0]["status"] = "done"
        app.on_event({"type": "visual.changed", "payload": {"job": client.visual_jobs_list[0]}})
        (tmp_path / "мой.png").write_bytes(PICTURE)
        view.chooseSource(QUrl.fromLocalFile(str(tmp_path / "мой.png")).toString())
        assert view.hasSource
        view.again()
        wait_until(lambda: not app.bg.busy and "from_png" in client.visual_calls[-1][1])
        sent = QImage.fromData(base64.b64decode(client.visual_calls[-1][1]["from_png"]), "PNG")  # sent as PNG
        assert sent.size() == QSize(32, 32) and sent.pixel(5, 7) == QImage.fromData(PICTURE).pixel(5, 7)
        view.chooseSource(QUrl.fromLocalFile(str(tmp_path / "нет.png")).toString())
        assert view.hasSource  # a file that is not there: the one chosen stays, the error said
        view.fromChat("ef" * 32)
        assert view.sourceSource == "image://pictures/blob/" + "ef" * 32
        view.clearSource()
        assert not view.hasSource and view.modelText.startswith("Z-Image-Turbo · ")
        view.fromChat("ef" * 32)
        client.visual_jobs_list[0]["status"] = "done"
        app.on_event({"type": "visual.changed", "payload": {"job": client.visual_jobs_list[0]}})
        view.draw()
        wait_until(lambda: not app.bg.busy and "from_blob" in client.visual_calls[-1][1])
        assert client.visual_calls[-1][1]["from_blob"] == "ef" * 32
        view.clearSource()

        # 29.8б: a video - «Видео» beside «Картинка» once a video model is here; it plays from the hub's file
        assert not view.hasVideo
        client.visual_videos = [{"model_id": "wan", "name": "Wan2.2 TI2V 5B Turbo", "tier": "video", "size": 832,
                                 "steps": 4, "cfg": 1.0}]  # fmt: skip
        view.load()
        wait_until(lambda: not app.bg.busy and view.hasVideo)
        assert [m["id"] for m in view.modes] == ["picture", "video"]
        view.setMode("video")
        view.setShape("wide")
        view.setQuality("fast")
        assert view.modelText == "Wan2.2 TI2V 5B Turbo · 832 × 480 · 2 с"
        view.setLength("3")
        view.setQuality("quality")
        assert view.modelText == "Wan2.2 TI2V 5B Turbo · 1280 × 704 · 3 с"
        view.draw()
        wait_until(lambda: not app.bg.busy and client.visual_calls[-1][0] == "video")
        assert client.visual_calls[-1][1] == {"prompt": "рыжая лиса в снегу", "model": "wan", "shape": "wide",
                                              "seconds": 3.0, "hd": True, "seed": -1}  # fmt: skip
        filmed = client.visual_jobs_list[0]
        app.on_event({"type": "visual.changed", "payload": {"job": {**filmed, "status": "running"}}})
        assert view.statusText == "снимаю видео…" and view.busy
        clip = app.paths.media / "2026-11" / f"{filmed['id']}.webp"
        clip.parent.mkdir(parents=True, exist_ok=True)
        frame = QImage(32, 32, QImage.Format.Format_RGB32)
        frame.fill(Qt.GlobalColor.darkCyan)
        assert frame.save(str(clip), "WEBP")  # one frame stands for the engine's animated WebP
        finished = {**filmed, "status": "done", "seconds": 21.7, "file": f"2026-11/{filmed['id']}.webp"}
        app.on_event({"type": "visual.changed", "payload": {"job": finished}})
        assert view.done and view.videoSource == QUrl.fromLocalFile(str(clip)).toString()
        assert view.pictureSource == "" and view.statusText == "готово за 22 с"
        view.saveAs(str(tmp_path / "лиса.webp"))
        assert (tmp_path / "лиса.webp").read_bytes() == clip.read_bytes()
        view.saveAs(QUrl.fromLocalFile(str(tmp_path / "лиса")).toString())  # 29.8в: MP4 unless WebP is chosen
        wait_until(lambda: not app.bg.busy and (tmp_path / "лиса.mp4").exists())
        assert (tmp_path / "лиса.mp4").read_bytes() == b"MP4 of " + filmed["id"].encode()
        assert any(r["id"] == filmed["id"] and r["video"] for r in view.recent)
        view.setMode("picture")

        # 29.6а: a picture a chat's model drew - in the chat, under its call
        blob = "cd" * 32
        client.histories["cp"] = {"id": "cp", "title": "Лиса", "busy": False, "projects": [], "messages": [
            {"id": 21, "role": "user", "model_id": None, "usage": None,
             "content": [{"type": "text", "text": "нарисуй лису"}]},
            {"id": 22, "role": "assistant", "model_id": "llama-swap/coder", "usage": None, "content": [
                {"type": "tool_call", "id": "g1", "name": "generate_image", "input": {"prompt": "a red fox"}}]},
            {"id": 23, "role": "tool", "model_id": None, "usage": None, "content": [
                {"type": "tool_result", "id": "g1", "name": "generate_image", "output": "Drawn: 640×384",
                 "error": False, "blob": blob, "media_type": "image/png", "size": 100}]},
            {"id": 24, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
             "content": [{"type": "text", "text": "Готово: лиса в снегу."}]}]}  # fmt: skip
        app.openChat("cp")
        wait_until(lambda: len(app.messages.rows) == 3)
        assert app.messages.rows[1]["tools"][0]["picture"] == f"image://pictures/blob/{blob}"
        wait_until(lambda: ("picture", blob) in client.visual_calls)  # the frame asked the hub for it
        assert view.provider.requestImage(f"blob/{blob}", QSize(), QSize()).width() == 32
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_an_install_going_on_shows_in_the_status_bar_from_the_start(qapp):
    """2026-10-28: the window learns of an install going on as it connects - the status bar shows it before the
    sheet is ever opened, and follows its progress."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.install_jobs_list = [{"id": "j9", "repo": "ggml-org/Qwen3-1.7B-GGUF", "quant": "Q8_0",
                                     "name": "Qwen3 1.7B Q8_0", "model_id": "qwen3-1.7b-q8_0", "status": "downloading",
                                     "done_bytes": 1_100_000_000, "total_bytes": 2_200_000_000,
                                     "percent": 50.0}]  # fmt: skip
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.install_view.status["name"])
        assert app.install_view.status["name"] == "Qwen3 1.7B Q8_0" and app.install_view.status["text"] == "50 %"
        progress = {"id": "j9", "done_bytes": 1_650_000_000, "total_bytes": 2_200_000_000, "speed": 22_000_000}
        app.on_event({"type": "install.progress", "payload": progress})
        assert app.install_view.status["text"] == "75 % · 22,0 МБ/с · ещё ≈ 25 с"
        for _ in range(10):
            QApplication.processEvents()
        assert quick.window.findChild(QObject, "statusInstall").property("visible")
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_models_question_in_a_chat_and_the_chats_rights_chip(qapp):
    """2026-10-28 (the user's word): a chat's model asks with answers to pick - the card over the input has them as
    buttons, the input answers it too; the chat's rights are a chip of the input, each explained; an agent's question
    has its answers as buttons."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.chat_questions = lambda cid: {"questions": []}
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("chats")
        opened(app)
        options = [{"label": "README.md", "description": "описание проекта"}, {"label": "notes.txt"}]
        send(app, "run.question", {"run_id": "c1", "id": "q7", "question": "Какой файл?", "options": options})
        for _ in range(10):
            QApplication.processEvents()
        card = quick.window.findChild(QObject, "chatQuestion")
        assert card.property("visible") and app.chatQuestion["question"] == "Какой файл?"
        assert [o["label"] for o in app.chatQuestion["options"]] == ["README.md", "notes.txt"]  # its buttons
        assert not [c for c in client.auto_calls if c[0] == "answer"]
        app.answerChat("README.md")  # a button
        wait_until(lambda: not app.bg.busy and ("answer", "c1", "q7", "README.md") in client.auto_calls)
        assert app.chatQuestion == {} and app.flashText == "Ответ передан модели"
        send(app, "run.question", {"run_id": "c1", "id": "q8", "question": "А дальше?", "options": []})
        app.send("  своими словами  ")  # the input answers it
        wait_until(lambda: not app.bg.busy and ("answer", "c1", "q8", "своими словами") in client.auto_calls)
        send(app, "run.question", {"run_id": "other", "id": "q9", "question": "?", "options": []})
        assert app.chatQuestion == {} and app.flashText.startswith("Модель спрашивает в «")  # another chat's

        assert app.chatModeName == "Спрашивать"
        items = app.chatModeItems
        assert [i["id"] for i in items[1:]] == ["read_only", "ask", "sandbox", "trusted"] and all(
            i["note"] for i in items[1:]) and items[2]["selected"]  # fmt: skip
        assert quick.window.findChild(QObject, "chatModeChip").property("text") == "Спрашивать"
        app.setChatMode("trusted")  # an open chat: saved at once
        wait_until(lambda: not app.bg.busy and ("context", "c1", {"mode": "trusted"}) in client.log)
        assert app.chatModeName == "Доверенный"
        app.newChat()
        app.setChatMode("read_only")  # a chat not made yet: with its first message
        assert app.chatModeName == "Только чтение" and not [e for e in client.log if e[0] == "context" and e[1] == "c2"]
        app.send("привет")
        wait_until(lambda: not app.bg.busy and client.sent)
        assert ("context", "c2", {"mode": "read_only"}) in client.log

        view = app.autonomy_view  # an agent's question: its answers as buttons
        client.run_list = [dict(AUTO_RUN)]
        plan = {**AUTO_PLAN, "questions": [{"id": "q1", "question": "Считать пустые?", "status": "pending",
                                            "options": [{"label": "Да"}, {"label": "Нет"}]}]}  # fmt: skip
        client.run_plan = lambda run_id: plan
        view.load()
        app.setMode("agent")
        view.select("auto1")
        wait_until(lambda: not app.bg.busy and view.task.get("question", {}).get("options"))
        for _ in range(10):
            QApplication.processEvents()
        assert [o["label"] for o in view.task["question"]["options"]] == ["Да", "Нет"]  # its buttons
        view.answer("q1", "Нет")
        wait_until(lambda: not app.bg.busy and ("answer", "auto1", "q1", "Нет") in client.auto_calls)
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_projects_sessions_stay_in_projects(qapp):
    """2026-10-28 (the user's word): «Проекты» has the project's own chats under its files - «Новый сеанс» begins one,
    it opens in place of the overview and never goes over to «Чаты»; «Чаты» lists only ordinary chats."""
    from datetime import UTC, datetime

    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        plain = client.conversations
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        def conversations():
            found = plain()
            return {**found, "conversations": [{"id": "s1", "title": "Сеанс проекта", "updated_at": now,
                                                "last_model_id": "llama-swap/coder", "home_project": "p1"},
                                               *found["conversations"]]}  # fmt: skip

        client.conversations = conversations
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.conversations and app.projects.items)
        app.projects.openProject("p1")
        wait_until(lambda: not app.bg.busy and app.projects.currentId == "p1")
        assert "s1" not in [c["id"] for c in app.chats]  # «Чаты»: ordinary chats only
        app.setMode("projects")
        for _ in range(10):
            QApplication.processEvents()
        assert [c["id"] for c in app.projectChats] == ["s1"] and not app.showProjectChat
        block = quick.window.findChild(QObject, "projectChats")
        assert block.property("visible") and app.projectChatsHeight == 0  # as tall as its chats need
        natural = block.property("height")
        app.setProjectChatsHeight(260)  # its line dragged up: kept in app.ini
        for _ in range(5):
            QApplication.processEvents()
        assert block.property("height") == 260 and int(app.settings.value("projects/chatsHeight")) == 260
        app.setProjectChatsHeight(10)  # too low: the list keeps its header and two rows
        for _ in range(5):
            QApplication.processEvents()
        assert block.property("height") == 76
        app.setProjectChatsHeight(0)  # a double click: back to as tall as needed
        for _ in range(5):
            QApplication.processEvents()
        assert block.property("height") == natural
        app.settings.remove("projects/chatsHeight")
        app.openProjectChat("s1")  # opens in «Проекты», in place of the overview
        wait_until(lambda: not app.bg.busy and app.currentId == "s1")
        assert app.mode == "projects" and app.showProjectChat
        # its requests show over it, as in «Чаты» (2026-10-28: a Claude Code session waited unseen)
        banner = quick.window.findChild(QObject, "approvalBanner")
        app.flash("")
        listing = {"command": "Get-ChildItem"}
        waiting = {**APPROVAL, "id": "ap-s1", "conversation_id": "s1", "tool": "PowerShell", "kind": "command",
                   "input": listing, "preview": {"kind": "command", "text": listing["command"]}}  # fmt: skip
        app.approvals.on_event("approval.requested", {"approval": waiting})
        wait_until(lambda: banner.property("opacity") == 1)
        assert app.approvals.card["id"] == "ap-s1" and "ждёт подтверждения" not in app.flashText
        app.showProjectOverview()
        wait_until(lambda: not banner.property("visible"))  # the overview: the shield says it
        app.openProjectChat("s1")
        app.approvals.on_event("approval.decided", {"approval": {**waiting, "status": "allowed"}})
        app.showProjectOverview()
        assert not app.showProjectChat and app.mode == "projects"

        app.newProjectChat()  # «Новый сеанс»
        assert app.mode == "projects" and app.showProjectChat and app.currentId == ""
        app.send("что в этом проекте?")
        wait_until(lambda: not app.bg.busy and client.sent)
        assert ("create", "p1") in client.log  # made as the project's session
        assert app.mode == "projects" and app.showProjectChat and app.currentId == "c2"  # it stayed in «Проекты»

        app.newChatAbout("p1")  # the overview's chat button: a session, still in «Проекты»
        assert app.mode == "projects" and app.showProjectChat and app.currentId == ""
        app.setMode("chats")  # «Чаты» never shows a project's session
        assert app.currentId == "" and not app.showProjectChat
        app.openChat("s1")  # a session opened from elsewhere (a card, the palette): in its project
        wait_until(lambda: not app.bg.busy and app.currentId == "s1")
        assert app.mode == "projects" and app.showProjectChat
        app.openChat("c1")  # an ordinary chat from «Проекты»: back to «Чаты»
        wait_until(lambda: not app.bg.busy and app.currentId == "c1")
        assert app.mode == "chats" and not app.showProjectChat
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_file_opens_over_the_projects_session(qapp):
    """2026-10-29 (the user's word: «почему нельзя открыть файлы внутри проекта»): with a project's session open, a
    file chosen in the tree or found by search loaded behind the session and never showed. It shows now; «Обзор
    проекта» shows the overview, the open file closed."""
    from datetime import UTC, datetime

    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        plain = client.conversations
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        session = {"id": "s1", "title": "Сеанс", "updated_at": now, "home_project": "p1"}
        client.conversations = lambda: {**plain(), "conversations": [session, *plain()["conversations"]]}
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.conversations and app.projects.items)
        app.projects.openProject("p1")
        app.setMode("projects")
        wait_until(lambda: not app.bg.busy and app.projects.currentId == "p1")
        app.openProjectChat("s1")
        wait_until(lambda: not app.bg.busy and app.showProjectChat)
        code = quick.window.findChild(QObject, "codeView")
        app.projects.openFile("README.md", False)  # a click in the tree
        wait_until(lambda: not app.bg.busy and app.projects.openPath == "README.md")
        assert not app.showProjectChat and app.mode == "projects" and code.property("visible")
        app.openProjectChat("s1")
        wait_until(lambda: not app.bg.busy and app.showProjectChat)
        app.projects.openFileAt("src/app.py", 2)  # a search result
        wait_until(lambda: not app.bg.busy and app.projects.openPath == "src/app.py")
        assert not app.showProjectChat and code.property("visible")
        app.showProjectOverview()  # «Обзор проекта»: the overview, not the file
        assert app.projects.openPath == "" and quick.window.findChild(QObject, "projectOverview").property("visible")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_sidebar_is_as_wide_as_dragged(qapp):
    """2026-10-28 (the user's word): the sidebar's right edge moves; its width is kept, within limits; a double click
    gives the usual width back."""
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        quick.window.resize(1280, 860)
        sidebar = quick.window.findChild(QObject, "sidebar")
        assert app.sidebarWidth == 264 and sidebar.property("width") == 264
        assert quick.window.findChild(QObject, "sidebarGrip") is not None
        app.setSidebarWidth(380)
        for _ in range(5):
            QApplication.processEvents()
        assert sidebar.property("width") == 380 and int(app.settings.value("ui/sidebarWidth")) == 380
        app.setSidebarWidth(50)  # too narrow: the least it may be
        assert app.sidebarWidth == 200
        app.setSidebarWidth(5000)  # too wide: the most, and the window keeps its main area
        for _ in range(5):
            QApplication.processEvents()
        assert app.sidebarWidth == 560 and sidebar.property("width") == 560
        app.setSidebarWidth(0)  # a double click
        assert app.sidebarWidth == 264
        app.settings.remove("ui/sidebarWidth")
        assert quick.warnings == []
    finally:
        quick.close()


def test_what_the_hub_keeps_in_the_settings(qapp):
    """2026-10-28 (D30, Q4): «Настройки → Хаб» says how long and how much the hub keeps and what it takes now; the
    limits are set there, and «Очистить старое сейчас» prunes at once."""
    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy and app.retention)
        kept = app.retention
        assert (kept["days"], kept["maxGb"]) == ("90", "2")
        assert kept["usage"] == ("снимки файлов 12 МБ · снимки экрана 0 МБ · журналы терминалов 3,0 ГБ · "
                                 "файлы из чатов 0 МБ · картинки 0 МБ")  # fmt: skip
        app.setRetention("90", "2")  # nothing changed: nothing sent
        app.setRetention("тридцать", "2")
        assert app.flashError and not [e for e in client.log if e[0] == "retention"]
        app.setRetention("30", "1,5")
        wait_until(lambda: not app.bg.busy and ("retention", 30, 1.5) in client.log)
        assert app.retention["days"] == "30" and app.retention["maxGb"] == "1,5"
        app.pruneKept()
        wait_until(lambda: not app.bg.busy and ("prune",) in client.log)
        assert app.flashText == "Удалено старых файлов: 3"
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_projects_folder_moved_gone_and_back_in_the_feed():
    """2026-10-28: the feed says when a project's folder was renamed (and the hub followed it), went, came back."""
    from app.ui.feed_rows import describe

    def said(kind, **payload):
        row = describe({"type": kind, "payload": {"name": "SiteTest", **payload}}, projects={}, chats={})
        return row["title"], row["detail"], row["level"]

    assert said("project.moved", **{"from": "F:/a", "to": "F:/b"}) == (
        "Папка проекта «SiteTest» переименована — путь обновлён",
        "F:/a → F:/b",
        "",
    )
    assert said("project.missing", path="F:/b") == ("Папка проекта «SiteTest» пропала", "F:/b", "warn")
    assert said("project.found", path="F:/b") == ("Папка проекта «SiteTest» снова на месте", "F:/b", "")


def test_links_the_hub_found_by_itself_in_the_feed():
    """2026-10-28: the hub looks for links again after a change; what it proposed goes into the feed."""
    from app.ui.feed_rows import describe

    found = [{"a": "front", "b": "back", "kind": "uses"}, {"a": "back", "b": "front", "kind": "shares"}]
    row = describe({"type": "links.proposed", "payload": {"added": 2, "links": found}}, projects={}, chats={})
    assert row["title"] == "Хаб нашёл возможные связи проектов: 2"
    assert row["detail"] == "front → back, использует; back → front, общее — проверьте в обзоре проекта"
    many = describe({"type": "links.proposed", "payload": {"added": 4, "links": found * 2}}, projects={}, chats={})
    assert many["detail"].startswith("front → back, использует; back → front, общее; front → back, использует… —")


def test_a_file_over_a_megabyte_in_pieces():
    """24.1: up to WHOLE_MAX_BYTES (the editor's limit) the viewer shows a file as one text - selected through the
    whole of it, coloured by the editor's highlighter; a bigger one (the hub gives up to 2 MB of it) comes in pieces of
    CHUNK_LINES, plain: one text costs ~0.4 s a megabyte to lay out (2026-10-28: only the pieces on screen are)."""
    assert project_rows.WHOLE_MAX_BYTES == editor.EDIT_LIMIT
    n = project_rows.CHUNK_LINES
    line = "x = 'строка'  # " + "-" * 40 + "\n"
    whole = line * (project_rows.WHOLE_MAX_BYTES // len(line.encode()))
    assert "text" in project_rows.code_view(whole, "a.py") and "text" in project_rows.diff_view(whole)
    text = "\n\n" + "a\r\n" * (project_rows.WHOLE_MAX_BYTES // 2)  # over the limit, Windows line ends
    view = project_rows.code_view(text, "long.py")
    first, second = view["chunks"][:2]
    assert "text" not in view and view["lexer"] == "" and view["digits"] == 6
    assert first["code"].startswith("\n\na\na") and "<" not in first["code"]  # plain; blank lines at the start kept
    assert first["numbers"].split("\n")[:3] == ["     1", "     2", "     3"]
    assert second["start"] == 2 + 2 * (n - 2)  # where the piece starts in the whole text, line ends as plain ones
    assert all(c["code"].count("\n") == n - 1 for c in view["chunks"][:-1])
    assert "chunks" in project_rows.diff_view("+" + text) and project_rows.diff_view("+" + text)["lexer"] == ""


def test_models_found_on_the_drives_are_listed_and_added(app):
    """«Установка моделей → На этом компьютере» (15.1, D49): the search on all drives with how it goes, the models
    found with whether each fits, «Добавить» - where it lies; one in llama-swap already says so."""
    view = app.install_view
    view.setTab("local")
    wait_until(lambda: not app.bg.busy)
    assert view.localModels == [] and "лежат на ваших дисках" in view.scanText and not view.scanning
    view.scanLocal()
    wait_until(lambda: view.scanning)
    send(app, "install.found", {"job": {"status": "scanning", "folders": 5000, "files": 2, "drive": "D:\\"}})
    assert view.scanText == "Ищу на дисках: D:\\ — просмотрено папок: 5000, найдено файлов: 2…"
    app.client.found = {"job": {"status": "done", "files": 2}, "at": "2026-11-04T20:00:00Z", "models": [
        {"name": "Qwen3-8B-Q4_K_M", "path": "D:\\LLM\\Qwen3-8B-Q4_K_M.gguf", "size": 4_900_000_000, "quant": "Q4_K_M",
         "projector": "", "source": "file", "verdict": "gpu", "words": "поместится в видеопамять", "in_config": ""},
        {"name": "gemma3:4b", "path": "C:\\Users\\u\\.ollama\\models\\blobs\\sha256-aa", "size": 3_300_000_000,
         "quant": "", "projector": "C:\\x\\sha256-bb", "source": "ollama", "verdict": "gpu", "words": "поместится",
         "in_config": "gemma3"}]}  # fmt: skip
    send(app, "install.found", {"job": {"status": "done", "files": 2}})
    wait_until(lambda: len(view.localModels) == 2)
    qwen, gemma = view.localModels
    assert view.scanText == "Найдено моделей: 2. Список хранится до следующего поиска." and not view.scanning
    assert (qwen["note"], qwen["words"], qwen["tone"], qwen["inConfig"]) == (
        "4,9 ГБ · Q4_K_M",
        "поместится в видеопамять",
        "ok",
        "",
    )
    assert gemma["note"] == "3,3 ГБ · понимает картинки · Ollama" and gemma["inConfig"] == "gemma3"
    view.addLocal(qwen["path"], qwen["projector"], qwen["name"])
    wait_until(lambda: ("local", qwen["path"], "", "Qwen3-8B-Q4_K_M") in app.client.install_calls)
    wait_until(lambda: "«Qwen3-8B-Q4_K_M» добавляется в llama-swap" in app._flash)  # 34.5: no trial said
    view.addLocal(gemma["path"], gemma["projector"], gemma["name"])  # 34.2: its id comes from Ollama's name
    wait_until(lambda: ("local", gemma["path"], gemma["projector"], "gemma3:4b") in app.client.install_calls)
    view.addFile("file:///D:/LLM/other.gguf")  # «Добавить файл…»: any GGUF on this computer, by its file's name
    wait_until(lambda: any(c[0] == "local" and c[1].endswith("other.gguf") and c[3] == ""
                           for c in app.client.install_calls))  # fmt: skip


def test_a_models_file_shown_in_explorer(app, tmp_path, monkeypatch):
    """34.3 (2026-11-08, the user's word: «показать этот файл в проводнике (это касается и для уже загруженных модели и
    найденных)»): Explorer opens with the file picked out - a model on the drives, one added, one downloaded; a file
    not whole yet (a download on pause: only its .part) - its folder; nothing there - words."""
    from pathlib import Path

    from app.ui import controller as ui_controller
    from app.ui.install_view import job_rows

    started, opened = [], []
    monkeypatch.setattr(ui_controller.subprocess, "Popen", lambda args, **_: started.append(args))
    monkeypatch.setattr(ui_controller.QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())))
    model = tmp_path / "LLM" / "Mine Q8_0.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF")
    app.showFile(str(model))
    assert started == [["explorer", "/select,", str(model)]] and opened == []
    app.showFile(str(tmp_path / "LLM" / "half.gguf"))
    assert opened == [tmp_path / "LLM"] and len(started) == 1
    app.showFile(str(tmp_path / "gone" / "x.gguf"))
    assert app.flashError and str(tmp_path / "gone" / "x.gguf") in app.flashText and len(opened) == 1

    # «Установки»: a model downloaded or added - «Показать в проводнике» beside its other buttons
    base = {"id": "j1", "name": "Tiny", "model_id": "tiny", "repo": "test/Tiny-GGUF", "folder": str(model.parent),
            "files": [{"path": "BF16/Mine Q8_0.gguf", "role": "model"}, {"path": "mmproj.gguf", "role": "mmproj"}],
            "own_files": 1, "error": "", "trial": {}}  # fmt: skip
    engine, drawn = {"path": "sd.zip", "role": "engine"}, {"path": "Mine Q8_0.gguf", "role": "diffusion-model"}
    picture = {**base, "id": "p1", "status": "done", "files": [engine, drawn]}
    jobs = [{**base, "status": s} for s in ("done", "failed", "paused", "downloading")] + [picture]
    rows = {r["id"] + r["status"]: r for r in job_rows(jobs)}
    assert [a["id"] for a in rows["j1done"]["actions"]] == ["show", "remove"]
    assert [a["id"] for a in rows["j1failed"]["actions"]] == ["resume", "show", "forget"]
    assert [a["id"] for a in rows["j1paused"]["actions"]] == ["resume", "show", "cancel"]
    assert "show" not in [a["id"] for a in rows["j1downloading"]["actions"]]  # still coming: nothing to show yet
    assert rows["j1done"]["file"] == rows["p1done"]["file"] == str(model)  # in its folder; a picture's - not the engine
    assert rows["j1done"]["actions"][0]["label"] == "Показать в проводнике"
    app.install_view._jobs = [{**base, "status": "done"}]
    app.install_view.act("j1", "show")
    assert started[-1] == ["explorer", "/select,", str(model)]


def test_an_install_gone_from_the_config_and_one_waiting_for_the_card_say_so():
    """34.4 (2026-11-08): «установлена», though tuning had written over the config and its block was gone - the row
    says the model is not in the config and offers to write it again or remove it. An install whose trial waits for
    the graphics card (tuning measures, a chat answers) - «ждёт пробного запуска», in the status bar too."""
    from app.ui.install_view import job_rows, status_line

    base = {"id": "j1", "name": "Gemma", "model_id": "gemma4-12b", "repo": "", "folder": "C:\\m",
            "files": [{"path": "sha256-bb", "role": "model"}], "own_files": 0, "error": "", "trial": {},
            "status": "done", "lost": True}  # fmt: skip
    lost = job_rows([base])[0]
    assert lost["statusWords"] == "нет в config.yaml" and "убрана из config.yaml" in lost["error"]
    assert [(a["id"], a["label"]) for a in lost["actions"]] == [
        ("resume", "Вернуть в llama-swap"), ("show", "Показать в проводнике"), ("remove", "Удалить")]  # fmt: skip
    kept = job_rows([{**base, "lost": False}])[0]
    assert kept["statusWords"] == "установлена" and kept["error"] == ""
    waiting = {**base, "lost": False, "status": "trialwait", "own_files": 1, "total_bytes": 9, "done_bytes": 9}
    row = job_rows([waiting])[0]
    assert row["statusWords"] == "ждёт пробного запуска" and row["live"] and [a["id"] for a in row["actions"]] == [
        "show"]  # fmt: skip
    assert status_line([waiting])["text"] == "ждёт пробного запуска"


def test_the_install_sheet_opened_before_the_hub_is_up_loads_when_it_comes(app, monkeypatch):
    """35.1 (2026-11-08, the user's screenshot after an update: «почему при каждом обновлением такое сообщение»): an
    update restarts the hub, and the window is up 16 s before it. The sheet opened meanwhile asked nothing and never
    asked again - «Смотрю, что умеет этот компьютер…» stayed. It loads when the hub connects; a request that fails
    (the hub still starting) is asked again."""
    from app.ui import install_view

    view, client = app.install_view, app.client
    app.client = None  # the window is up, the hub is not
    view.load()
    assert view.systemText == "Смотрю, что умеет этот компьютер…" and not view.catalog
    app.attach(client, start_events=False)
    wait_until(lambda: not app.bg.busy and view.catalog and view.systemText.startswith("RTX 5080"))

    monkeypatch.setattr(install_view, "RETRY_MS", 10)
    view._system = {}
    answer, tries = client.install_system, []

    def starting():
        tries.append(1)
        if len(tries) < 3:
            raise HubError("the hub is starting")
        return answer()

    client.install_system = starting
    view.load()
    wait_until(lambda: view.systemText.startswith("RTX 5080"))
    assert len(tries) == 3


def test_models_added_are_tried_by_the_button(app):
    """34.5 (2026-11-08, the user's word): «Добавить» without the trial - «добавлена, не проверена», with its own
    «Пробный запуск» in «Установки»; «Пробный запуск (N)» beside «Добавить файл…» asks the hub to try them all in the
    order added, the line beside says which one goes and how many wait, «Остановить» takes back those not begun."""
    from app.ui.install_view import job_rows

    view = app.install_view
    first = {"id": "a1", "name": "A", "model_id": "a", "repo": "", "folder": "D:\\m", "own_files": 0, "error": "",
             "files": [{"path": "A.gguf", "role": "model"}], "trial": {}, "status": "added",
             "created_at": "2026-11-08T13:00:00.000Z"}  # fmt: skip
    second = {**first, "id": "a2", "name": "B", "model_id": "b", "created_at": "2026-11-08T13:01:00.000Z"}
    row = job_rows([first])[0]
    assert row["statusWords"] == "добавлена, не проверена" and not row["live"]
    assert [(a["id"], a["label"]) for a in row["actions"]] == [
        ("trial", "Пробный запуск"), ("show", "Показать в проводнике"), ("remove", "Удалить")]  # fmt: skip
    app.client.install_jobs_list = [second, first]  # the hub lists newest first
    view._load_jobs()
    wait_until(lambda: not app.bg.busy and view.untried == 2)
    assert view.trialText == "" and view.activeCount == 0
    view.tryAll()
    wait_until(lambda: not app.bg.busy and app.client.install_calls[-1] == ("trial",))
    assert view.untried == 0 and view.trialText == "Пробный запуск: «A» · ещё в очереди: 1"
    assert "Пробный запуск: 2" in app.flashText
    send(app, "install.changed", {"job": {**first, "status": "trial"}})
    assert view.trialText == "Пробный запуск: «A» · ещё в очереди: 1"
    send(app, "install.changed", {"job": {**first, "status": "done", "trial": {"seconds": 2.0}}})
    assert view.trialText == "Пробный запуск: «B»"
    app.client.install_jobs_list = [{**second, "status": "trialwait"}]  # what the hub has waiting now
    view.stopTrials()
    wait_until(lambda: not app.bg.busy and app.client.install_calls[-1] == ("trial-stop",))
    assert view.trialText == "" and view.untried == 1
    view.act("a2", "trial")  # one model's own button in «Установки»
    wait_until(lambda: not app.bg.busy and app.client.install_calls[-1] == ("trial", "a2"))
    assert view.trialText == "Пробный запуск: «B»"


def test_a_long_llama_swap_id_stays_out_of_the_rows_text(qapp):
    """34.1 (2026-11-08, the user's screenshot): an Ollama model added under its blob's name («sha256-» and 64
    characters) - «в llama-swap: …» took all the width it wanted, the row's text was squeezed to the left, and the line
    «7,5 ГБ · Q4_K_M · Ollama · помещается…», which had no width of its own, ran under it. The id takes at most 40 %
    of the row, cut in the middle; the line under the name stays in its column."""
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtQuick import QQuickItem

    from app.ui.main import QuickApp

    blob = "sha256-" + "4efb729a" * 8
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.found = {"job": {"status": "done"}, "at": "2026-11-08T13:00:00Z", "models": [
            {"name": "hf.co/IlyaGusev/saiga_nemo_12b-GGUF:Q4_K_M", "path": "C:\\Users\\u\\.ollama\\models\\blobs\\"
             + blob, "size": 7_500_000_000, "quant": "Q4_K_M", "projector": "", "source": "ollama", "verdict": "gpu",
             "words": "помещается в видеопамять — быстро", "in_config": blob}]}  # fmt: skip
        added = {"id": "a1", "name": "A", "model_id": "a", "repo": "", "folder": "D:\\m", "own_files": 0, "error": "",
                 "files": [], "trial": {}, "status": "added", "created_at": "2026-11-08T13:00:00.000Z"}  # fmt: skip
        client.install_jobs_list = [added, {**added, "id": "a2", "name": "B"}]  # 34.5: two not tried yet
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        view = app.install_view
        sheet = quick.window.findChild(QObject, "installSheet")
        app.openUi("install-models")
        view.setTab("local")
        wait_until(lambda: sheet.property("opened") and not app.bg.busy and view.localModels)
        for _ in range(10):
            QApplication.processEvents()
        name = view.localModels[0]["name"]
        listed = quick.window.findChild(QQuickItem, "installLocal")
        row, said, note, show = (item_named(listed, f"{part}_{name}")
                                 for part in ("localRow", "localInConfig", "localNote", "localShow"))  # fmt: skip

        def left(item):
            return item.mapToScene(QPointF(0, 0)).x()

        assert said.property("text") == f"в llama-swap: {blob}" and said.property("truncated")
        assert said.width() <= 0.4 * row.width() + 1 and left(said) + said.width() <= left(row) + row.width()
        shown = [t for t in note.childItems() if t.isVisible()]
        assert [t.property("text") for t in shown] == ["7,5 ГБ · Q4_K_M · Ollama",
                                                       "· помещается в видеопамять — быстро"]  # fmt: skip
        # 34.3: «Показать в проводнике» between the text and the id; nothing runs under either
        assert show.isVisible() and show.property("tip") == "Показать в проводнике"
        assert left(show) + show.width() <= left(said)
        assert all(left(t) + t.width() <= left(show) - 6 for t in shown)
        # 34.5: «Пробный запуск (N)» beside «Добавить файл…» for the models added and not tried
        trial, stop = (quick.window.findChild(QQuickItem, name) for name in ("tryAdded", "stopTrials"))
        assert trial.isVisible() and trial.property("text") == "Пробный запуск (2)" and not stop.isVisible()
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_finished_install_is_not_undone_by_a_stale_list(app):
    """2026-10-28 (a flaky test found it): the jobs list loading while «установлена» arrives - the list's answer was
    read before, says «загружается», and used to overwrite the event: the status bar would keep a finished install
    going. What events say during a load goes over its answer; one forgotten meanwhile does not come back."""
    view = app.install_view
    stale = {"id": "j1", "name": "Gemma", "status": "downloading", "done_bytes": 1, "total_bytes": 2, "percent": 50.0,
             "error": "", "trial": {}, "files": []}  # fmt: skip
    app.client.install_jobs_list = [stale, {**stale, "id": "j2", "name": "Qwen"}]
    view._load_jobs()  # on its way: it answers with the list as it was
    app.on_event({"type": "install.changed", "payload": {"job": {**stale, "status": "done", "percent": 100.0}}})
    app.on_event({"type": "install.forgotten", "payload": {"id": "j2"}})
    wait_until(lambda: not app.bg.busy and not view._jobs_loading)
    assert [(j["id"], j["status"]) for j in view._jobs] == [("j1", "done")] and view.activeCount == 0


def test_the_sandbox_hunks_in_the_window(qapp):
    """17.4 (D51): in the sandbox sheet a changed file shows its hunks, each with a tick; the file's tick says all,
    a part or none - a click on a part takes all of it again, a hunk of a file that was off takes only itself; the
    apply carries the hunks ticked, a coordinator's too."""
    from PySide6.QtCore import QMetaObject, QObject

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)
        view = app.sandbox_view
        one = [{"index": 0, "header": "@@ -0,0 +1 @@", "text": "+# Новое\n"}]
        conf_py = {"path": "conf.py", "status": "changed", "hunks": PARTED_HUNKS}
        client.sandboxed = [{"project_id": "p1", "name": "LocalChat", "exists": True, "diff": "",
                             "files": [conf_py, {"path": "new.md", "status": "added", "hunks": one}]}]  # fmt: skip
        app.setChatMode("sandbox")
        wait_until(lambda: not app.bg.busy and view.count == 2)
        row = view.projects[0]
        conf, new = row["files"]
        assert row["hunked"] and conf["choosable"] and not new["choosable"]
        assert (conf["state"], [h["ticked"] for h in conf["hunks"]]) == ("all", [True, True, True])
        view.tickHunk(conf["key"], 1)
        row = view.projects[0]
        assert row["files"][0]["state"] == "part" and row["applyText"] == "Применить отмеченное"
        sheet = quick.window.findChild(QObject, "sandboxSheet")
        QMetaObject.invokeMethod(sheet, "open")
        wait_until(lambda: sheet.property("opened"))
        body = sheet.property("contentItem")
        wait_until(lambda: item_named(body, "sandboxHunk_conf.py_2") is not None)
        assert item_named(body, "sandboxFile_new.md") is not None and item_named(body, "sandboxHunk_new.md_0") is None
        item_named(body, "sandboxFile_conf.py").clicked.emit()  # a part: the file's tick takes all of it again
        assert view.projects[0]["files"][0]["state"] == "all"
        view.tick(conf["key"])
        assert view.projects[0]["files"][0]["state"] == "none"
        item_named(body, "sandboxHunk_conf.py_2").clicked.emit()  # a hunk of a file that was off: only itself
        conf = view.projects[0]["files"][0]
        assert conf["state"] == "part" and [h["ticked"] for h in conf["hunks"]] == [False, False, True]
        view.tick(new["key"])
        assert view.projects[0]["applyText"] == "Применить выбранные (1 из 2)"
        view.apply("p1")
        wait_until(lambda: not app.bg.busy and view.count == 0)
        assert client.sandbox_calls[-1] == ("apply", "c1", "p1", ["conf.py"], {"conf.py": [2]})
        assert view._skipped == set() and view._skipped_hunks == {}

        changed = {"project_id": "p1", "name": "proj", "files": [conf_py], "diff": "", "exists": True}
        client.task_sandboxes = [{"task_id": "a", "agent": "Правщик", "status": "done", "run_id": "r1",
                                  "projects": [changed]}]  # fmt: skip
        app.on_event({"type": "run.finished", "payload": {"run": {"id": "r1", "parent_run": "c1"}}})
        wait_until(lambda: not app.bg.busy and view.taskCount == 1)
        view.tickHunk(view.tasks[0]["files"][0]["key"], 0)
        assert view.taskApplyText == "Применить отмеченное"
        view.applyAll()
        wait_until(lambda: not app.bg.busy and view.taskCount == 0)
        assert client.sandbox_calls[-1] == ("apply all", "c1", None, {"a": {"p1": {"conf.py": [1, 2]}}})
        QMetaObject.invokeMethod(sheet, "close")
        for _ in range(10):
            QApplication.processEvents()
        assert quick.warnings == []
    finally:
        quick.close()


def test_applying_while_the_model_writes_says_it_goes_after_the_turn():
    """19.7: «Применить» while the model writes waits for the turn's end - the window says so, and then what went."""
    from app.ui.sandbox_view import applied_text

    waits = applied_text({"applied": [], "conflicts": [], "kept": [], "deferred": True})
    assert waits == "Применится после хода: модель ещё пишет"
    went = applied_text({"applied": ["a.txt"], "conflicts": [], "kept": ["b.txt"], "after_turn": True})
    assert went == "Применено к проекту: 1 файлов Остальные файлы (1) ждут в песочнице."


def test_the_sandbox_says_what_of_its_diff_is_not_shown():
    """The audit (D46): the sheet shows the diff's first lines - and now says so; «Применить» takes every file."""
    from app.ui.approvals import PREVIEW_LINES
    from app.ui.sandbox_view import sandbox_rows

    def row(diff, chars=0):
        return sandbox_rows([{"project_id": "p", "name": "proj", "files": [{"path": "a.py", "status": "changed"}],
                              "diff": diff, "diff_chars": chars or len(diff)}])[0]  # fmt: skip

    assert row("+x\n" * 10)["more"] == "" and len(row("+x\n" * 10)["lines"]) == 10
    long = "+x\n" * (PREVIEW_LINES + 50)
    assert row(long)["more"].startswith(f"Показаны первые {PREVIEW_LINES} строк из {PREVIEW_LINES + 50} — «Применить»")
    assert "с лишним" in row(long, chars=len(long) * 3)["more"]  # the hub cut it too


def test_the_viewer_tells_the_line_ends_and_invisible_characters():
    """The audit (D46): the file's header says CRLF / LF and warns of bidi or zero-width characters."""
    from app.ui.project_rows import file_view

    plain = file_view({"path": "a.txt", "size": 4, "text": "a\r\nb\r\n", "encoding": "utf-8", "eol": "CRLF"})
    assert plain["eol"] == "CRLF" and plain["hidden"] == ""
    trojan = file_view({"path": "auth.py", "size": 40, "encoding": "utf-8", "eol": "LF",
                        "text": "ok = 1\nif role == 'user\u202e':\n    x = '\u200b'\n"})  # fmt: skip
    assert trojan["hidden"] == "⚠ невидимые символы: 2 (строки 2, 3)"
    assert file_view({"path": "b.txt", "size": 3, "text": "\ufeffok", "encoding": "utf-8"})["hidden"] == ""
