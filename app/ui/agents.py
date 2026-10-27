"""The hub's own agents in the window (stage 5, D16): profiles to make and change, tasks to give, runs to follow.

A run is a conversation, so "Открыть" shows it in the chat view with its tool calls; its approvals come to the
queue like a chat's. The panel lists the profiles, edits one at a time (a draft until saved: each save is a new
version in the hub), starts a task for the chosen profile, and lists the latest runs with their status.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .project_rows import local_time

if TYPE_CHECKING:
    from .controller import Controller

MODES = {"read_only": "только чтение", "ask": "спрашивать", "trusted": "доверенный"}
STATUSES = {
    "queued": ("в очереди", "dots"),
    "running": ("работает", "sparkle"),
    "waiting": ("ждёт подтверждения", "shield"),
    "done": ("готово", "check"),
    "error": ("ошибка", "close"),
    "cancelled": ("отменён", "stop"),
}
TOOL_LABELS = {
    "list_files": "смотреть папки",
    "read_file": "читать файлы",
    "search_code": "искать в коде",
    "git_status": "git: состояние",
    "git_diff": "git: изменения",
    "terminal_output": "читать терминалы",
    "list_processes": "смотреть процессы",
    "find_symbol": "искать определения",
    "file_outline": "структура файлов",
    "edit_file": "править файлы",
    "write_file": "записывать файлы",
    "run_command": "выполнять команды",
    "web_fetch": "читать страницы в сети",
    "delegate_task": "давать задачи другим агентам (координатор)",
}
BLANK = {"id": "", "name": "", "instructions": "", "model_id": "", "tools": [], "mode": "ask", "project_ids": [],
         "max_steps": 30, "max_minutes": 30, "budget_usd": None}  # fmt: skip


def profile_rows(profiles: list[dict[str, Any]], models: dict[str, str], editing: str) -> list[dict[str, Any]]:
    return [{
        "id": p["id"],
        "name": p["name"],
        "note": " · ".join(s for s in (
            models.get(p["model_id"], p["model_id"]),
            MODES.get(p["mode"], p["mode"]),
            f"проектов: {len(p['project_ids'])}" if p["project_ids"] else "без проектов",
            f"версия {p['version']}",
        ) if s),
        "selected": p["id"] == editing,
    } for p in profiles]  # fmt: skip


def run_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for r in runs:
        label, icon = STATUSES.get(r.get("shown_status") or r["status"], (r["status"], "dots"))
        if r["status"] == "error" and r.get("detail"):
            label += f": {r['detail']}"
        parts = [r["profile_name"], local_time(r.get("created_at", ""))]
        if r.get("parent_name"):
            parts.insert(1, f"подзадача от «{r['parent_name']}»")
        if r.get("steps"):
            parts.append(f"шагов {r['steps']}")
        if r.get("cost_usd"):
            parts.append(f"${r['cost_usd']:.4f}")
        rows.append({
            "id": r["id"],
            "task": " ".join(r["task"].split()),
            "status": label,
            "icon": icon,
            "level": "error" if r["status"] == "error" else "warn" if r.get("shown_status") == "waiting" else "",
            "note": " · ".join(parts),
            "active": r["status"] in ("queued", "running"),
        })  # fmt: skip
    return rows


def number(text: str, low: int, high: int) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


class Agents(QObject):
    changed = Signal()
    runsChanged = Signal()
    draftChanged = Signal()
    finished = Signal(str, str)  # a run ended: a notice outside the window when it is not in front (D21)

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._profiles: list[dict[str, Any]] = []
        self._tools: list[str] = []
        self._runs: list[dict[str, Any]] = []
        self._draft: dict[str, Any] = dict(BLANK)
        self._estimate = ""  # what a task of the chosen agent may cost (D12)

    # ================= for QML =================

    @Property(list, notify=changed)
    def profiles(self) -> list:
        models = {m["id"]: m["name"] for m in self.app.model_list}
        return profile_rows(self._profiles, models, self._draft["id"])

    @Property(list, notify=runsChanged)
    def runs(self) -> list:
        return run_rows(self._runs)

    @Property("QVariantMap", notify=draftChanged)
    def draft(self) -> dict:
        models = {m["id"]: m["name"] for m in self.app.model_list}
        d = self._draft
        return {**d, "budget": "" if d["budget_usd"] is None else f"{d['budget_usd']:g}",
                "modelName": models.get(d["model_id"], d["model_id"] or "выберите модель"),
                "saved": bool(d["id"])}  # fmt: skip

    @Property(str, notify=draftChanged)
    def estimate(self) -> str:
        return self._estimate

    def load_estimate(self) -> None:
        agent_id = self._draft["id"]
        self._estimate = ""
        if not agent_id:
            return

        def show(found: dict[str, Any]) -> None:
            if agent_id == self._draft["id"] and found.get("estimate_usd") is not None:
                cost = found["estimate_usd"]
                self._estimate = ("бесплатно (локальная модель)" if not cost else f"≈ ${cost:.4f}") + \
                    f" — {found['basis']}"  # fmt: skip
                self.draftChanged.emit()

        self.app._call(lambda: self.app.client.agent_estimate(agent_id), show, lambda _e: None)

    @Property(list, notify=draftChanged)
    def toolChoices(self) -> list:
        chosen = set(self._draft["tools"])
        return [{"id": t, "name": TOOL_LABELS.get(t, t), "selected": not chosen or t in chosen} for t in self._tools]

    @Property(list, notify=draftChanged)
    def projectChoices(self) -> list:
        chosen = set(self._draft["project_ids"])
        return [{"id": p["id"], "name": p["name"], "selected": p["id"] in chosen} for p in self.app.projects.items]

    @Property(list, notify=changed)
    def modelChoices(self) -> list:
        """The models an agent may have: the hub's, not Claude Code (an agent of its own)."""
        return [m for m in self.app.pickerItems if not str(m.get("id", "")).startswith("claude-code/")]

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        def show(answer: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
            listing, runs = answer
            self._profiles, self._tools, self._runs = listing["agents"], listing["tools"], runs
            if self._draft["id"] and all(p["id"] != self._draft["id"] for p in self._profiles):
                self._draft = dict(BLANK)
            if not self._draft["id"] and not self._draft["name"] and self._profiles:
                self._draft = dict(self._profiles[0])
            self.load_estimate()
            self.changed.emit()
            self.runsChanged.emit()
            self.draftChanged.emit()

        self.app._call(lambda: (self.app.client.agents(), self.app.client.runs()), show, lambda _e: None)

    def load_runs(self) -> None:
        def show(runs: list[dict[str, Any]]) -> None:
            self._runs = runs
            self.runsChanged.emit()

        self.app._call(lambda: self.app.client.runs(), show, lambda _e: None)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "agents.changed":
            self.load()
        elif kind.startswith("run.") or (kind.startswith("approval.") and self._runs):
            self.load_runs()
            if kind == "run.finished":
                run = payload.get("run") or {}
                label = STATUSES.get(run.get("status", ""), ("", ""))[0]
                said = f"Агент «{run.get('profile_name', '')}»: {label}"
                self.app.flash(said, error=run.get("status") == "error")
                self.finished.emit(said, " ".join(str(run.get("detail") or run.get("task", "")).split())[:200])

    # ================= the draft =================

    @Slot()
    def newProfile(self) -> None:
        self._draft = {**BLANK, "model_id": self.app.role_model("agents")}  # the agents' model (D39), if set
        self.changed.emit()
        self.draftChanged.emit()

    @Slot(str)
    def editProfile(self, profile_id: str) -> None:
        found = next((p for p in self._profiles if p["id"] == profile_id), None)
        if found is not None:
            self._draft = dict(found)
            self.load_estimate()
            self.changed.emit()
            self.draftChanged.emit()

    @Slot(str, str)
    def setField(self, name: str, value: str) -> None:
        if name in ("name", "instructions"):
            self._draft[name] = value
        elif name == "mode" and value in MODES:
            self._draft["mode"] = value
        elif name == "model_id":
            self._draft["model_id"] = value
        elif name == "max_steps" and (n := number(value, 1, 200)) is not None:
            self._draft["max_steps"] = n
        elif name == "max_minutes" and (n := number(value, 1, 1440)) is not None:
            self._draft["max_minutes"] = n
        elif name == "budget":
            try:
                self._draft["budget_usd"] = float(value.replace(",", ".")) if value.strip() else None
            except ValueError:
                return
        else:
            return
        self.draftChanged.emit()

    @Slot(str)
    def toggleTool(self, tool: str) -> None:
        chosen = list(self._draft["tools"]) or list(self._tools)  # [] means all: start from all of them
        chosen = [t for t in chosen if t != tool] if tool in chosen else [*chosen, tool]
        self._draft["tools"] = [t for t in self._tools if t in chosen]
        self.draftChanged.emit()

    @Slot(str)
    def toggleProject(self, project_id: str) -> None:
        ids = list(self._draft["project_ids"])
        self._draft["project_ids"] = [p for p in ids if p != project_id] if project_id in ids else [*ids, project_id]
        self.draftChanged.emit()

    @Slot()
    def save(self) -> None:
        body = {k: self._draft[k] for k in BLANK if k != "id"}
        profile_id = self._draft["id"]

        def done(profile: dict[str, Any]) -> None:
            self._draft = dict(profile)
            self.app.flash(f"Агент «{profile['name']}» сохранён, версия {profile['version']}")
            self.load()

        def failed(error: Exception) -> None:
            self.app.show_error(f"Агент не сохранён: {error}")

        self.app._call(lambda: self.app.client.save_agent(body, profile_id or None), done, failed)

    @Slot()
    def deleteProfile(self) -> None:
        profile_id, name = self._draft["id"], self._draft["name"]
        if not profile_id:
            return

        def decide(yes: bool) -> None:
            if yes:
                self._draft = dict(BLANK)
                self.app._call(lambda: self.app.client.delete_agent(profile_id), lambda _r: self.load())

        self.app._ask(f"Удалить агента «{name}»?", "Его прогоны останутся в списке чатов.", "Удалить", decide)

    # ================= runs =================

    @Slot(str)
    def startRun(self, task: str) -> None:
        profile_id = self._draft["id"]
        if not profile_id or not task.strip():
            return

        def done(run: dict[str, Any]) -> None:
            self.app.flash(f"Задача отдана агенту «{run['profile_name']}»")
            self.load_runs()
            self.app.load_conversations()

        def failed(error: Exception) -> None:
            self.app.show_error(f"Прогон не начался: {error}")

        self.app._call(lambda: self.app.client.start_run(profile_id, task), done, failed)

    @Slot(str)
    def cancelRun(self, run_id: str) -> None:
        self.app._call(lambda: self.app.client.cancel_run(run_id), lambda _r: self.load_runs(),
                       lambda error: self.app.show_error(f"Не остановлен: {error}"))  # fmt: skip

    @Slot(str)
    def openRun(self, run_id: str) -> None:
        self.app.setMode("chats")
        self.app.openChat(run_id)
