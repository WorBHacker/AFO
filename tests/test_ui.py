"""The Qt Quick window: its controller against a fake hub client, its row builders, and the real QML loaded with
history and a streamed answer (no QML warnings allowed). Offscreen, software scene graph."""

import copy
import json
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
                  "note": "прочитан · проверка: 9 строк"},
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
        self.ticket = ""
        self.chosen = []
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
        self.schedule_calls = []
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

    def answer_question(self, run_id, question_id, answer):
        self.auto_calls.append(("answer", run_id, question_id, answer))
        return {"id": question_id}

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
                "encoding": "utf-8"}  # fmt: skip

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

    def delete_rule(self, rule_id):
        self.rule_list = [r for r in self.rule_list if r["id"] != rule_id]

    def revert_change(self, change_id, *, force=False):
        if self.stale and not force:
            raise HubError("src/app.py has changed since: undoing would lose those changes")
        self.log.append(("revert", change_id, force))
        self.change_list = [{**c, "reverted_at": "2026-10-27T05:00:00Z"} for c in self.change_list]
        return self.change_list[0]

    def answer_approval(self, approval_id, *, allow, scope="once", comment=""):
        if approval_id == "gone":
            raise HubError("no open approval gone: it was answered already, or never asked")
        self.answers.append((approval_id, allow, scope, comment))
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
    """Open reasoning shows in parts cut at line ends, so a tick lays out only the last part (one TextEdit of 100k
    characters laid all of it out on every tick). Read back, the parts are exactly the reasoning."""
    import random

    from app.ui.main import QuickApp

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        app.attach(FakeClient(), start_events=False)
        wait_until(lambda: not app.bg.busy)
        opened(app)

        def parts() -> list[str]:
            quick.window.grabWindow()  # a frame: the list lays out (and creates) the rows it shows
            todo, found = [quick.window.contentItem()], []
            while todo:
                item = todo.pop(0)
                todo.extend(item.childItems())
                if item.objectName() == "thinkingParts":
                    found.append(
                        [child.property("text") for child in item.childItems() if child.property("part") is not None]
                    )
            return next(p for p in found if p and p[0].startswith("Старт"))

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
    ]}  # fmt: skip
    shown = connection_rows(data, {"t": {"ok": False, "text": "Telegram: Unauthorized"}})
    assert [r["status"] for r in shown] == ["задан", "http://127.0.0.1:8888", "не задан"]
    assert shown[2]["note"] == "Telegram: Unauthorized" and shown[2]["noteOk"] is False


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
        for name in (
            "palette",
            "budgetsSheet",
            "settingsSheet",
            "renameDialog",
            "approvalsList",
            "agentsSheet",
            "connectionsSheet",
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
    code = project_rows.code_view("".join(f"x = {n}\n" for n in range(10)) + "\tlong = 1\n", "a.py")
    # coloured with tags Qt reads fast (no CSS spans); the lexer's last newline is not an empty last line
    assert code["rich"] and code["lines"] == 11 and "<font color=" in code["code"] and "<span" not in code["code"]
    assert code["code"].endswith("1</font></pre>")
    assert " 9\n10" in code["numbers"] and "<" not in code["numbers"]  # plain, padded: they line up on the right
    assert code["columns"] == project_rows.TAB_COLUMNS + len("long = 1")  # the width, without a layout
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
    assert projects.openPath == "src/app.py"  # what the tree's rows read: not the whole file each
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
    assert projects.file == {} and projects.openPath == ""


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
        # its width comes from the longest line (no layout asked for it): a long Cyrillic or tabbed line still fits
        app.client.files["wide.py"] = "x = '" + "широкая строка " * 30 + "'\n\t\tif x:\n"
        app.projects.openFile("wide.py", False)
        wait_until(lambda: app.projects.file.get("path") == "wide.py")
        for _ in range(10):
            QApplication.processEvents()
        todo, body = [code], None
        while todo:
            item = todo.pop()
            todo.extend(item.childItems())
            body = item if item.metaObject().className() == "QQuickTextEdit" else body
        assert body.property("width") >= body.property("implicitWidth") > 2000
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
        assert quick.window.findChild(QObject, "agentQuestion").property("visible")
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
        view.newTask()
        view.setFlag("network", True)
        view.setField("goal", "Скачать курс валют")
        view.start()
        wait_until(lambda: not app.bg.busy and client.auto_calls)
        assert client.auto_calls[0][1]["network"] is True
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
        window = {"id": 42, "title": "Тестовое окно агента", "rect": [100, 120, 360, 420]}
        control = {"run_id": "r1", "window": window, "program": "Python (тест)", "action": "type"}
        app.on_event({"type": "computer.control", "payload": control})
        assert view.active and view.text == "Агент управляет компьютером · Python (тест)"
        wait_until(lambda: frame.property("visible") and bar.property("visible"))
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
