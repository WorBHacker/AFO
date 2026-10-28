"""The window's logic for the QML view: talks to the hub, keeps what the view shows, takes the view's commands.

Everything goes through the hub (D2, D31): REST calls run off the GUI thread (Background), answers stream in as
events (EventThread). Streamed deltas are kept for every chat, so switching chats mid-answer loses nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Property, QObject, QSettings, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from common.paths import DataPaths

from .. import presenter
from ..events import EventThread
from ..hub_client import HubClient, HubError, connect
from ..tasks import Background
from . import commands, mentions, rows
from .agent_keys_view import AgentKeys
from .agents import Agents
from .approvals import Approvals
from .autonomy import AutonomyView
from .check_view import CheckView
from .composer import Mentions
from .computer_view import ComputerView
from .connections import Connections
from .feed import Feed
from .install_view import InstallView
from .mail_view import MailView
from .mcp_view import McpView
from .memory_view import MemoryView
from .model_setup import ModelSetup
from .models import RowModel
from .programs import Programs
from .projects import Projects
from .sandbox_view import SandboxView
from .schedule_view import SchedulesView
from .setup_view import SetupView
from .terminals import Terminals
from .theme import Theme

log = logging.getLogger("app.ui")

ANTHROPIC_KEY = "anthropic_api_key"
RENDER_INTERVAL_MS = 80  # a streamed answer is re-rendered at most ~12 times a second
RENDER_SHARE = 4  # ...and a render takes at most a quarter of the GUI thread: a long answer renders less often
RENDER_INTERVAL_MAX_MS = 1000
THINK_TICK_MS = 100
FLASH_MS = 6000
MODES = ("chats", "projects", "agent")
NOT_CONNECTED = "Хаб ещё не подключён"
HUB_STATES = {
    "connecting": "подключение…",
    "up": "подключён",
    "lost": "нет связи, переподключение…",
    "down": "недоступен",
    "stopped": "остановлен",
}


@dataclass
class LiveRun:
    """An answer being streamed: what arrived so far, and when it started thinking and answering."""

    model_id: str
    text: str = ""
    reasoning: str = ""
    loading: str = ""
    clock_from: float = field(default_factory=time.monotonic)
    answer_at: float | None = None

    @property
    def think_elapsed(self) -> float:
        return (self.answer_at or time.monotonic()) - self.clock_from

    @property
    def thinking(self) -> bool:
        return bool(self.reasoning) and not self.text


class Controller(QObject):
    chatsChanged = Signal()
    currentChanged = Signal()  # another chat (or a new one) is open
    headerChanged = Signal()  # its title or the line under it: not a switch, the view keeps its place
    modelChanged = Signal()
    effortChanged = Signal()
    busyChanged = Signal()
    hubChanged = Signal()
    statusChanged = Signal()
    sessionChanged = Signal()
    flashChanged = Signal()
    questionChanged = Signal()
    budgetsChanged = Signal()
    keyChanged = Signal()
    autostartChanged = Signal()
    modeChanged = Signal()
    chatProjectsChanged = Signal()
    contextChanged = Signal()
    chatRefusalChanged = Signal()  # the chat's model or its own choice of Strata's refusal vector
    chatQuestionChanged = Signal()  # the open chat's model asks (ask_user, Claude Code's AskUserQuestion)
    projectChatChanged = Signal()  # «Проекты»: the project's sessions, and whether one is open in the main area
    claudeCodeChanged = Signal()
    statsChanged = Signal()
    draftRestored = Signal(str)  # a message that did not go out comes back to the composer
    uiRequested = Signal(str)  # the view should open something: "models", "budgets", "settings", "rename", ...
    budgetError = Signal(str)  # the hub refused a budget (e.g. a warning above the limit)

    def __init__(self, paths: DataPaths, settings: QSettings, theme: Theme, parent: QObject | None = None):
        super().__init__(parent)
        self.paths = paths
        self.settings = settings
        self.theme = theme
        self.bg = Background(self)
        self.bg.error.connect(self.show_error)
        self.client: HubClient | None = None
        self.events: EventThread | None = None
        self.models: dict[str, dict[str, Any]] = {}
        self.roles: dict[str, str] = {}  # the general settings' roles (D39): chat, cards, agents, fallback...
        self.model_list: list[dict[str, Any]] = []
        self.conversations: list[dict[str, Any]] = []
        self.busy: set[str] = set()
        self._stale_runs: set[str] = set()  # answers streaming when the link to the hub broke (_on_connected)
        self._interrupted: set[str] = set()  # chats whose answer the hub lost, not yet shown so (_end_dead_run)
        self.live: dict[str, LiveRun] = {}
        self._run_models: dict[str, str] = {}  # each running chat's model: a run's answers after tool steps, too
        self.current: str | None = None
        self.messages = RowModel(self)
        self.session = presenter.Session()
        self.totals: dict[str, Any] | None = None
        self.budgets: list[dict[str, Any]] = []
        self._key_set = False
        self._autostart: dict[str, Any] = {}
        self._status = rows.status_parts(None)
        self._hub_state = "connecting"
        self._title = "Новый чат"
        self._meta = ""
        self._count = 0  # the open chat: messages, money spent, the model of its last answer
        self._spent = 0.0
        self._last_author = ""
        self._flash = ""
        self._flash_error = False
        self._question: dict[str, str] = {}
        self._pending = None
        self._errors = 0
        self._link: bool | None = None  # events connection: never up yet / up / lost
        self._model_id = str(settings.value("chat/model") or "")
        self._effort = str(settings.value("chat/effort", "medium"))
        mode = str(settings.value("ui/mode", "chats"))
        self._mode = mode if mode in MODES else "chats"
        self._render_timer = self._timer(RENDER_INTERVAL_MS, self._render_live, single=True)
        self._think_timer = self._timer(THINK_TICK_MS, self._render_live)
        self._flash_timer = self._timer(FLASH_MS, lambda: self._set_flash(""), single=True)
        theme.changed.connect(self._restyle)
        self.projects = Projects(self)
        self.terminals = Terminals(self)
        self.projects.projectChanged.connect(lambda: self.terminals.set_project(self.projects.currentId))
        self.feed = Feed(self)
        self._chat_projects: list[str] = []  # the open chat's projects (a new chat's, until its first message)
        self._context_messages: list[dict[str, Any]] = []  # the open chat's stored context (system messages)
        self._context_settings: dict[str, Any] = {"map": True, "live": True, "pinned": []}
        self._context_rows: list[dict[str, Any]] = []
        self._chat_ask: dict[str, Any] = {}  # the open chat's question waiting for the user: {id, question, options}
        self._project_chat = False  # «Проекты»: the open session in the main area instead of the project's overview
        self._new_home = ""  # a session not made yet: its project (it is made with its first message)
        self.modelChanged.connect(self.chatProjectsChanged)  # a cloud model sees local-only projects by name
        for signal in (self.chatsChanged, self.currentChanged, self.modeChanged, self.projects.projectChanged):
            signal.connect(self.projectChatChanged)
        self.projects.projectChanged.connect(self._project_switched)
        self.modelChanged.connect(self.chatRefusalChanged)  # the refusal switch: only a model with Strata's vector
        self.contextChanged.connect(self.chatRefusalChanged)
        self.projects.listChanged.connect(self.chatProjectsChanged)
        self.projects.projectChanged.connect(lambda: self.feed.set_project(self.projects.currentId))
        self.mentions = Mentions(self)
        self.approvals = Approvals(self)
        self.agents = Agents(self)  # the hub's own agents (stage 5, D16)
        self.connections = Connections(self)  # keys and tokens behind the password (D13)
        self.programs = Programs(self)  # which programs the autonomous agent may control (D43)
        self.model_setup = ModelSetup(self)  # the models' settings (stage 8, D39)
        self.autonomy_view = AutonomyView(self)  # the Agent mode: the autonomous agent's tasks (stage 9, D40)
        self.autonomy_view.selectionChanged.connect(self.approvals._refresh)  # its approvals card follows the task
        self.memory_view = MemoryView(self)  # the agent's memory: facts and skills (stage 9.3)
        self.mcp_view = McpView(self)  # outside MCP servers, a section of Connections (9.5, D42)
        self.computer_view = ComputerView(self)  # the agent at the desktop: frame and «Стоп» (9.6, D41)
        self.schedules_view = SchedulesView(self)  # goals on a schedule and on events (9.7, Q23)
        self.mail_view = MailView(self)  # the agent's mailbox, a section of Connections (9.4b, Q21)
        self.agent_keys = AgentKeys(self)  # the user's API keys for the agent's requests (9.4b)
        self.sandbox_view = SandboxView(self)  # «Песочница»: a chat's changes in its worktree (D17, 9.4b)
        self.install_view = InstallView(self)  # local models from Hugging Face (10.1, D37)
        self.setup_view = SetupView(self)  # the first-run wizard (10.2)
        self.check_view = CheckView(self)  # «Проверить систему» and the logs for support (10.6)
        self.setup_view.openRequested.connect(lambda: self.uiRequested.emit("setup"))
        self.contextChanged.connect(self.sandbox_view.load)  # another chat, or its mode changed
        self._claude_code: dict[str, Any] = {}  # is the user's Claude Code CLI there (stage 4, D8)
        self._stats: dict[str, Any] = {}  # what the hub learnt about its models and spending (stage 6, D11, D12)
        self._advice: dict[str, Any] = {}  # which model for the open chat (D11): shown, never switched to by itself
        self._priority = str(settings.value("chat/priority", "balanced"))

    def _timer(self, interval: int, slot, *, single: bool = False) -> QTimer:
        timer = QTimer(self)
        timer.setInterval(interval)
        timer.setSingleShot(single)
        timer.timeout.connect(slot)
        return timer

    # ================= properties for QML =================

    @Property(QObject, constant=True)
    def messageModel(self) -> RowModel:
        return self.messages

    @Property(list, notify=chatsChanged)
    def chats(self) -> list:
        """«Чаты»: the ordinary chats - a project's sessions live in «Проекты» (2026-10-28, the user's word)."""
        return rows.chat_rows([c for c in self.conversations if not c.get("home_project")], self.busy, self.models)

    # ---------- a project's sessions: its chats, in «Проекты» (2026-10-28, the user's word) ----------

    @Property(list, notify=projectChatChanged)
    def projectChats(self) -> list:
        pid = self.projects.currentId
        return rows.chat_rows([c for c in self.conversations if pid and c.get("home_project") == pid], self.busy,
                              self.models) if pid else []  # fmt: skip

    @Property(bool, notify=projectChatChanged)
    def showProjectChat(self) -> bool:
        """«Проекты» shows the open session (or a new one being written) instead of the project's overview."""
        return self._mode == "projects" and self._project_chat and bool(self.projects.currentId)

    def _home_of(self, conversation_id: str | None) -> str:
        return next((c.get("home_project") or "" for c in self.conversations if c["id"] == conversation_id), "")

    @Slot()
    def newProjectChat(self) -> None:
        """«Новый сеанс»: a chat of the open project - it stays in «Проекты»."""
        pid = self.projects.currentId
        if not pid:
            return
        self.newChat()
        self._set_chat_projects([pid], push=False)
        self._new_home, self._project_chat = pid, True
        self.projectChatChanged.emit()

    @Slot(str)
    def openProjectChat(self, conversation_id: str) -> None:
        self._project_chat = True
        self.openChat(conversation_id)
        self.projectChatChanged.emit()

    @Slot()
    def showProjectOverview(self) -> None:
        self._project_chat = False
        self.projectChatChanged.emit()

    def _project_switched(self) -> None:
        """Another project chosen: its overview, unless the open session is its own."""
        if self._project_chat and self._home_of(self.current) != self.projects.currentId:
            self._project_chat = False

    @Property(str, notify=currentChanged)
    def currentId(self) -> str:
        return self.current or ""

    @Property(str, notify=headerChanged)
    def currentTitle(self) -> str:
        return self._title

    @Property(str, notify=headerChanged)
    def currentMeta(self) -> str:
        return self._meta

    @Property(str, notify=modelChanged)
    def modelId(self) -> str:
        return self._model_id

    @Property(str, notify=modelChanged)
    def modelName(self) -> str:
        return rows.model_name(self.models, self._model_id) if self._model_id in self.models else "Нет моделей"

    @Property(bool, notify=modelChanged)
    def modelCloud(self) -> bool:
        return self._model_id in self.models and not rows.is_local(self.models, self._model_id)

    @Property(list, notify=modelChanged)
    def pickerItems(self) -> list:
        return rows.picker_items(self.model_list, set(self._status["loaded"]), self._model_id, self._advice)

    @Property(str, notify=modelChanged)
    def modelCountText(self) -> str:
        n = len(self.model_list)
        return f"{n} {presenter.plural(n, 'модель', 'модели', 'моделей')}"

    @Property(str, notify=effortChanged)
    def effort(self) -> str:
        return self._effort

    @Property(str, notify=effortChanged)
    def effortLabel(self) -> str:
        return dict(presenter.EFFORTS).get(self._effort, self._effort)

    @Property(list, constant=True)
    def efforts(self) -> list:
        return [{"value": v, "label": label} for v, label in presenter.EFFORTS]

    @Property(bool, notify=busyChanged)
    def busyNow(self) -> bool:
        return self.current is not None and self.current in self.busy

    @Property(str, notify=hubChanged)
    def hubState(self) -> str:
        return self._hub_state

    @Property(list, notify=statsChanged)
    def statsRows(self) -> list:
        names = {m["id"]: m["name"] for m in self.model_list}
        return rows.stats_rows(self._stats.get("models") or [], names)

    @Property(str, notify=statsChanged)
    def forecastText(self) -> str:
        return rows.forecast_text(self._stats.get("spending"))

    @Slot()
    def loadStats(self) -> None:
        def show(found: dict[str, Any]) -> None:
            self._stats = found
            self.statsChanged.emit()

        self._call(lambda: self.client.stats(), show, lambda _e: None)

    @Property(bool, notify=claudeCodeChanged)
    def claudeCodeInstalled(self) -> bool:
        return bool(self._claude_code.get("installed"))

    @Property(str, notify=hubChanged)
    def hubPort(self) -> str:
        return presenter.hub_port(self.client.base_url if self.client else None)

    @Property(str, notify=hubChanged)
    def hubTooltip(self) -> str:
        where = self.client.base_url if self.client else "адрес ещё не известен"
        return f"Хаб: {HUB_STATES[self._hub_state]}\n{where}"

    @Property("QVariantMap", notify=statusChanged)
    def status(self) -> dict:
        return self._status

    @Property(str, notify=sessionChanged)
    def sessionText(self) -> str:
        return self.session.text()

    @Property(str, notify=sessionChanged)
    def sessionTooltip(self) -> str:
        tooltip = self.session.tooltip(self.totals)
        if self.budgets:
            tooltip += "\n\nБюджеты:\n" + "\n".join(presenter.budget_line(b) for b in self.budgets)
        return tooltip

    @Property(bool, notify=sessionChanged)
    def sessionWarn(self) -> bool:
        return presenter.budgets_state(self.budgets) != "ok"

    @Property(str, notify=flashChanged)
    def flashText(self) -> str:
        return self._flash

    @Property(bool, notify=flashChanged)
    def flashError(self) -> bool:
        return self._flash_error

    @Property("QVariantMap", notify=questionChanged)
    def question(self) -> dict:
        return self._question

    @Property(list, notify=budgetsChanged)
    def budgetItems(self) -> list:
        return commands.budget_items(self.budgets)

    @Property(list, notify=budgetsChanged)
    def budgetScopes(self) -> list:
        return commands.budget_scopes(self.model_list, self.budgets)

    @Property(list, constant=True)
    def budgetPeriods(self) -> list:
        return [{"id": v, "name": label} for v, label in presenter.PERIODS]

    @Property(list, constant=True)
    def budgetActions(self) -> list:
        return [{"id": v, "name": label} for v, label in presenter.HARD_ACTIONS]

    @Property(bool, notify=keyChanged)
    def keySet(self) -> bool:
        return self._key_set

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._mode

    @Property(list, notify=chatProjectsChanged)
    def chatProjects(self) -> list:
        """The chat's projects: {id, name, hidden} - hidden when the chosen model is a cloud one and the project
        is local-only (the model then gets its name and nothing more, D10)."""
        known = {p["id"]: p for p in self.projects.items}
        cloud = self.modelCloud
        return [
            {"id": pid, "name": known[pid]["name"], "hidden": cloud and not known[pid].get("cloud_ok")}
            for pid in self._chat_projects
            if pid in known
        ]

    # ---------- the model's question in this chat, the chat's rights (2026-10-28) ----------

    @Property("QVariantMap", notify=chatQuestionChanged)
    def chatQuestion(self) -> dict:
        return self._chat_ask

    def _set_chat_question(self, question: dict[str, Any] | None) -> None:
        self._chat_ask = dict(question or {})
        self.chatQuestionChanged.emit()

    @Slot(str)
    def answerChat(self, text: str) -> None:
        """The answer to the open chat's question: an option picked, or the user's own words from the input."""
        cid, qid, text = self.current, self._chat_ask.get("id"), text.strip()
        if not (cid and qid and text):
            return
        self._set_chat_question(None)
        self._call(lambda: self.client.answer_question(cid, qid, text), lambda _r: self.flash("Ответ передан модели"),
                   lambda e: self.show_error(f"Ответ не принят: {e}"))  # fmt: skip

    def _load_chat_question(self, cid: str) -> None:
        def show(data: dict[str, Any]) -> None:
            if cid == self.current:
                pending = data.get("questions") or []
                self._set_chat_question(pending[-1] if pending else None)

        self._call(lambda: self.client.chat_questions(cid), show, lambda _e: None)

    @Property(str, notify=contextChanged)
    def chatModeName(self) -> str:
        return dict((m, n) for m, n, _h in rows.CHAT_MODES).get(self._context_settings.get("mode") or "ask", "")

    @Property(list, notify=contextChanged)
    def chatModeItems(self) -> list:
        return rows.chat_mode_items(self._context_settings.get("mode") or "ask")

    # ---------- Strata's refusal vector for this chat (2026-10-28) ----------

    @Property(bool, notify=chatRefusalChanged)
    def chatRefusalShown(self) -> bool:
        return rows.model_vector(self.models, self._model_id) is not None

    @Property(str, notify=chatRefusalChanged)
    def chatRefusalName(self) -> str:
        return rows.refusal_name(self.models, self._model_id, self._context_settings.get("refusal_vector") or "")

    @Property(list, notify=chatRefusalChanged)
    def chatRefusalItems(self) -> list:
        return rows.refusal_items(self.models, self._model_id, self._context_settings.get("refusal_vector") or "",
                                  "этого чата")  # fmt: skip

    @Slot(str)
    def setChatRefusal(self, choice: str) -> None:
        """This chat's own choice («model» - as the model is set), from its next answer on; a chat not made yet takes
        it with its first message."""
        value = "" if choice == "model" else choice
        if value not in ("", "on", "off"):
            return
        if self.current:
            self._save_context(refusal_vector=value)
        else:
            self._context_settings = {**self._context_settings, "refusal_vector": value}
            self.contextChanged.emit()
        self.flash(rows.refusal_name(self.models, self._model_id, value) + " — в этом чате со следующего ответа")

    @Property(list, notify=contextChanged)
    def contextBlocks(self) -> list:
        return self._context_rows

    @Property(int, notify=contextChanged)
    def contextTokens(self) -> int:
        return rows.context_total(self._context_messages)

    @Property("QVariantMap", notify=contextChanged)
    def contextSettings(self) -> dict:
        names = {p["id"]: p["name"] for p in self.projects.items}
        pinned = [
            {**p, "name": f"{names.get(p['project_id'], '?')}/{p['path']}"} for p in self._context_settings["pinned"]
        ]
        return {**self._context_settings, "pinned": pinned}

    @Property(list, notify=chatProjectsChanged)
    def chatProjectChoices(self) -> list:
        attached = set(self._chat_projects)
        return [
            {"id": p["id"], "name": p["name"], "note": p.get("description") or p["path"], "icon": "folder",
             "selected": p["id"] in attached}
            for p in self.projects.items
        ]  # fmt: skip

    @Property(str, constant=True)
    def dataDir(self) -> str:
        return str(self.paths.root)

    @Property(str, constant=True)
    def version(self) -> str:
        from common import __version__

        return __version__

    # ================= connecting =================

    @Slot()
    def connectHub(self) -> None:
        self._set_hub("connecting")
        self.flash("Подключение к хабу (если он не запущен, приложение запускает его)…")
        self.bg.run(lambda: connect(self.paths), self.attach, self._connect_failed)

    def _connect_failed(self, error: Exception) -> None:
        self._set_hub("down")
        self.show_error(f"Нет связи с хабом: {error}")

    def attach(self, client: HubClient, *, start_events: bool = True) -> None:
        if self.events is not None:
            self.events.stop()
            self.events = None
        self.client = client
        if start_events:
            self.events = EventThread(client.events_url, client.token, parent=self)
            self.events.event.connect(self.on_event)
            self.events.connected.connect(self._on_connected)
            self.events.start()
        self._set_hub("up")
        self._set_flash("")
        self.reload_all()

    def _on_connected(self, connected: bool) -> None:
        self._set_hub("up" if connected else "lost")
        if connected and self._link is False:
            # back after a break: deltas are not replayed, so fetch what happened. An answer that was streaming
            # and is not running now (the hub restarted) ended without an event: the status will say so.
            self._stale_runs = set(self.live)
            self.reload_all()
        self._link = connected

    def _set_hub(self, state: str) -> None:
        self._hub_state = state
        self.hubChanged.emit()

    def reload_all(self) -> None:
        self.load_models()
        self.load_conversations()
        self.refresh_status()
        self.refresh_usage()
        self.load_secrets()
        self.projects.load()
        self.terminals.load()
        self.approvals.load()
        self.programs.load()
        self.autonomy_view.load()
        self.schedules_view.load()
        self.install_view.load_jobs()  # an install going on: in the status bar before the sheet is opened
        self._call(lambda: self.client.claude_code(), self._set_claude_code, lambda _e: None)
        self._call(lambda: self.client.autostart(), self._set_autostart, lambda _e: None)
        self.setup_view.load()  # a computer without an engine for local models: the wizard opens (10.2)
        if self.current:
            self.openChat(self.current)

    def _set_autostart(self, found: dict[str, Any]) -> None:
        self._autostart = found
        self.autostartChanged.emit()

    @Property("QVariantMap", notify=autostartChanged)
    def autostart(self) -> dict:
        """login, llama_swap, llama_swap_found - how LocalChat starts (Q8)."""
        return self._autostart

    @Slot(str, bool)
    def setAutostart(self, what: str, on: bool) -> None:
        if what not in ("login", "llama_swap"):
            return

        def done(found: dict[str, Any]) -> None:
            self._set_autostart(found)
            if what == "login":
                self.flash(
                    "Хаб будет запускаться при входе в Windows" if on else "Автозапуск при входе в Windows выключен"
                )

        self._call(lambda: self.client.set_autostart(**{what: on}), done)

    @Slot(str, str)
    def chooseLlamaSwap(self, what: str, url: str) -> None:
        """llama-swap's config or its start (a script or llama-swap.exe) as a file chosen in the window; '' - back to
        the default (10.3)."""
        if what not in ("llama_swap_config", "llama_swap_start") or self.client is None:
            return
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url

        def done(found: dict[str, Any]) -> None:
            self._set_autostart(found)
            shown = found["llama_swap_config"] if what == "llama_swap_config" else found["llama_swap_script"]
            self.flash(f"{'Конфиг' if what == 'llama_swap_config' else 'Запуск'} llama-swap: {shown}")

        self.call_or_fail(lambda: self.client.set_autostart(**{what: path}), done,
                          lambda e: self.show_error(f"Не выбрано: {e}"))  # fmt: skip

    @Slot(str)
    def openFolder(self, path: str) -> None:
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _set_claude_code(self, status: dict[str, Any]) -> None:
        self._claude_code = status
        self.claudeCodeChanged.emit()

    def _call(self, fn, on_done=None, on_error=None) -> bool:
        """`fn` on a pool thread, its result to `on_done` on this one. False (and nothing runs) while no hub is
        connected: a caller that marked something as going on undoes it then (see `call_or_fail`)."""
        if self.client is None:
            self.show_error(NOT_CONNECTED)
            return False
        self.bg.run(fn, on_done, on_error)
        return True

    def call_or_fail(self, fn, on_done, on_error) -> None:
        """`_call`, and without a hub `on_error` at once: a "searching…" or "saving…" it would clear never stays."""
        if not self._call(fn, on_done, on_error):
            on_error(HubError(NOT_CONNECTED))

    def shutdown(self) -> None:
        if self.events is not None:
            self.events.stop()
        self.terminals.shutdown()
        self.bg.wait(3000)
        if self.client is not None:
            self.client.close()

    # ================= models =================

    def load_models(self) -> None:
        self._call(lambda: self.client.models(), self._set_models)
        self._call(lambda: self.client.model_settings(""), self._set_roles, lambda _e: None)

    def _set_roles(self, general: dict[str, Any]) -> None:
        """Which model does what (D39): a new chat, project cards, a new agent; the hub keeps the spare one."""
        self.roles = dict((general.get("settings") or {}).get("roles") or {})

    def role_model(self, role: str) -> str:
        """The model of a role when it is offered now, else ''."""
        model_id = self.roles.get(role, "")
        return model_id if model_id in self.models else ""

    @Property(str, notify=modelChanged)
    def priority(self) -> str:
        return self._priority

    @Slot(str)
    def setPriority(self, priority: str) -> None:
        """What the advice weighs most (D11): balanced, quality, speed or price."""
        if priority in ("balanced", "quality", "speed", "price") and priority != self._priority:
            self._priority = priority
            self.settings.setValue("chat/priority", priority)
            self.modelChanged.emit()
            self.load_advice()

    def load_advice(self) -> None:
        if self.client is None:
            return
        cid, ids, priority = self.current, list(self._chat_projects), self._priority

        def show(advice: dict[str, Any]) -> None:
            if cid == self.current:
                self._advice = advice
                self.modelChanged.emit()

        self._call(lambda: self.client.advice(conversation_id=cid, project_ids=None if cid else ids,
                                              priority=priority), show, lambda _e: None)  # fmt: skip

    def _set_models(self, models: list[dict[str, Any]]) -> None:
        self.model_list = models
        self.models = {m["id"]: m for m in models}
        if self._model_id not in self.models:
            ordered = presenter.sorted_models(models)
            self._model_id = ordered[0]["id"] if ordered else ""
        self.modelChanged.emit()
        self.chatsChanged.emit()

    @Slot(str)
    def chooseModel(self, model_id: str) -> None:
        if model_id in self.models and model_id != self._model_id:
            self._model_id = model_id
            self.settings.setValue("chat/model", model_id)
            self.modelChanged.emit()
            if self.models[model_id].get("effort"):  # the model's own reasoning depth (D39)
                self.chooseEffort(self.models[model_id]["effort"])

    @Slot(str)
    def chooseEffort(self, value: str) -> None:
        if value in dict(presenter.EFFORTS):
            self._effort = value
            self.settings.setValue("chat/effort", value)
            self.effortChanged.emit()

    @Slot()
    def syncModels(self) -> None:
        def done(result: dict) -> None:
            self.load_models()
            if result.get("errors"):
                self.flash(f"Список моделей обновлён, ошибки: {result['errors']}", error=True)
            else:
                self.flash("Список моделей обновлён")

        self._call(lambda: self.client.sync_models(), done)

    @Slot()
    def unloadLocal(self) -> None:
        self._call(lambda: self.client.unload_local(), lambda _r: self.flash("Локальная модель выгружена"))

    @Slot()
    def askUnloadLocal(self) -> None:
        """The sidebar's button: the loaded local model gives its VRAM and RAM back - asked first."""
        names = ", ".join(self._status.get("loaded") or [])

        def decide(yes: bool) -> None:
            if yes:
                self.unloadLocal()

        self._ask(f"Выгрузить {names or 'локальную модель'}?", "Освободятся видеопамять и оперативная память, которые "
                  "она занимает. Следующий запрос к ней загрузит её заново — это займёт от 20 секунд до минуты.",
                  "Выгрузить", decide)  # fmt: skip

    # ================= the machine, money =================

    def refresh_status(self) -> None:
        self._call(lambda: self.client.status(), self._show_status)

    def _show_status(self, status: dict[str, Any]) -> None:
        loaded_before = self._status["loaded"]
        self._status = rows.status_parts(status)
        self.statusChanged.emit()
        if self._status["loaded"] != loaded_before:
            self.modelChanged.emit()  # the picker marks what is in VRAM
        if "busy" in status:
            for cid in self._stale_runs - set(status["busy"]):
                self._end_dead_run(cid)
            self._stale_runs = set()
            self.busy = set(status["busy"]) | set(self.live)
            self.busyChanged.emit()

    def refresh_usage(self) -> None:
        def show(totals: dict[str, Any]) -> None:
            self.totals = totals
            self.sessionChanged.emit()

        self._call(lambda: self.client.usage(), show)
        self._call(lambda: self.client.budgets(), self._set_budgets)

    def _set_budgets(self, budgets: list[dict[str, Any]]) -> None:
        self.budgets = budgets
        self.sessionChanged.emit()
        self.budgetsChanged.emit()

    @Slot(str, str, str, str, str)
    def saveBudget(self, scope_key: str, period: str, soft: str, hard: str, action: str) -> None:
        """From the budgets panel: amounts come as text, empty means "no such limit"."""
        try:
            soft_usd = float(soft.replace(",", ".")) if soft.strip() else None
            hard_usd = float(hard.replace(",", ".")) if hard.strip() else None
        except ValueError:
            self.budgetError.emit("Суммы — числа в долларах, например 5 или 2.50")
            return
        scope, _, provider = scope_key.partition(":")
        body = {
            "scope": scope,
            "provider": provider,
            "period": period,
            "soft_usd": soft_usd,
            "hard_usd": hard_usd,
            "hard_action": action,
        }

        def done(_saved) -> None:
            self.flash("Бюджет сохранён")
            self._call(lambda: self.client.budgets(), self._set_budgets)

        self._call(lambda: self.client.set_budget(**body), done, lambda e: self.budgetError.emit(str(e)))

    @Slot(int)
    def deleteBudget(self, budget_id: int) -> None:
        self._call(
            lambda: self.client.delete_budget(budget_id),
            lambda _r: self._call(lambda: self.client.budgets(), self._set_budgets),
        )

    # ================= the Anthropic key (D13) =================

    def load_secrets(self) -> None:
        """Whether Claude's key is set (the picker's note); the keys themselves are the Connections sheet's."""
        self.connections.load()

    def show_connections(self, rows: list[dict]) -> None:
        key_set = any(r["name"] == ANTHROPIC_KEY and r["set"] for r in rows)
        if key_set != self._key_set:
            self._key_set = key_set
            self.keyChanged.emit()

    # ================= chats =================

    def load_conversations(self) -> None:
        self._call(lambda: self.client.conversations(), self._set_conversations)

    def _set_conversations(self, data: dict[str, Any]) -> None:
        self.busy |= set(data.get("busy", []))
        self.conversations = data["conversations"]
        self.chatsChanged.emit()
        self.busyChanged.emit()

    @Slot()
    def newChat(self) -> None:
        self._new_home = ""  # an ordinary chat (newProjectChat sets its project after this)
        if self.role_model("chat"):  # the model the user chose for new chats (D39)
            self.chooseModel(self.role_model("chat"))
        self.current = None
        self._title, self._meta = "Новый чат", ""
        self._count, self._spent, self._last_author = 0, 0.0, ""
        self.messages.reset([])
        self._think_timer.stop()
        self._set_chat_projects([], push=False)
        self._set_context([], None)
        self._set_chat_question(None)
        self.currentChanged.emit()
        self.headerChanged.emit()
        self.busyChanged.emit()

    @Slot(str)
    def newChatAbout(self, project_id: str) -> None:
        """A new session of this project (from its overview): it stays in «Проекты» (2026-10-28, the user's word)."""
        if project_id != self.projects.currentId:
            self.projects.openProject(project_id)
        self.setMode("projects")
        self.newProjectChat()

    @Slot(str)
    def toggleChatProject(self, project_id: str) -> None:
        ids = list(self._chat_projects)
        if project_id in ids:
            ids.remove(project_id)
        else:
            ids.append(project_id)
        self._set_chat_projects(ids)

    def _set_chat_projects(self, ids: list[str], *, push: bool = True) -> None:
        """The open chat's projects; `push` tells the hub (a chat not made yet takes them with its first message)."""
        self._chat_projects = list(ids)
        self.chatProjectsChanged.emit()
        self.load_advice()
        if push and self.current and self.client is not None:
            cid, wanted = self.current, list(ids)
            self._call(lambda: self.client.set_chat_projects(cid, wanted))

    def _set_context(self, messages: list[dict[str, Any]], settings: dict[str, Any] | None) -> None:
        self._context_messages = [m for m in messages if m["role"] == "system"]
        self._context_settings = settings or {"map": True, "live": True, "pinned": [], "mode": "ask"}
        self._context_rows = rows.context_rows(self._context_messages)
        self.contextChanged.emit()

    @Slot()
    def loadContext(self) -> None:
        """The open chat's context, read again (the inspector opens)."""
        cid = self.current
        if cid:
            self._call(
                lambda: self.client.conversation(cid),
                lambda data: self._set_context(data["messages"], data.get("context")) if cid == self.current else None,
            )

    @Slot(int)
    def toggleContextBlock(self, index: int) -> None:
        if 0 <= index < len(self._context_rows):
            row = self._context_rows[index]
            self._context_rows = [*self._context_rows[:index], {**row, "open": not row["open"]},
                                  *self._context_rows[index + 1:]]  # fmt: skip
            self.contextChanged.emit()

    @Slot(str, bool)
    def setContextLayer(self, layer: str, on: bool) -> None:
        """The map or the live state on or off for this chat, from its next message on."""
        if layer in ("map", "live") and self.current:
            self._save_context(**{layer: on})

    @Slot(str)
    def setChatMode(self, mode: str) -> None:
        """The chat's rights (D17): read_only, ask, sandbox, trusted - for the model's next writes; a chat not made
        yet takes them with its first message."""
        if mode not in ("read_only", "ask", "sandbox", "trusted"):
            return
        if self.current:
            self._save_context(mode=mode)
        else:
            self._context_settings = {**self._context_settings, "mode": mode}
            self.contextChanged.emit()

    @Slot(str)
    def setChatInstructions(self, text: str) -> None:
        """The chat's own system prompt (D39): after the general and the model's, from its next message on."""
        if self.current and text.strip() != (self._context_settings.get("instructions") or ""):
            self._save_context(instructions=text.strip())

    @Slot(str)
    def setChatPreset(self, preset: str) -> None:
        """«Точно / Баланс / Творчески» for this chat over the model's parameters; '' - as the model has them."""
        if self.current and preset in ("", "precise", "balanced", "creative"):
            self._save_context(preset=preset)

    @Slot(str, str)
    def pinFile(self, project_id: str, path: str) -> None:
        if not self.current:
            self.show_error("Закрепить файл можно в открытом чате: откройте чат, к которому он нужен")
            return
        if project_id not in self._chat_projects:
            self._set_chat_projects([*self._chat_projects, project_id])
        pinned = [*self._context_settings["pinned"], {"project_id": project_id, "path": path}]
        self._save_context(pinned=pinned)
        self.flash(f"Файл {path} будет в контексте чата со следующего сообщения")

    @Slot(int)
    def unpinFile(self, index: int) -> None:
        pinned = list(self._context_settings["pinned"])
        if 0 <= index < len(pinned):
            pinned.pop(index)
            self._save_context(pinned=pinned)

    def _save_context(self, **changes: Any) -> None:
        cid = self.current
        self._context_settings = {**self._context_settings, **changes}
        self.contextChanged.emit()

        def done(settings: dict[str, Any]) -> None:
            if cid == self.current:
                self._context_settings = settings
                self.contextChanged.emit()

        self._call(lambda: self.client.set_context(cid, **changes), done)

    @Slot(int, int)
    def toggleTool(self, row_index: int, tool_index: int) -> None:
        """Opens or closes a tool call's result in the transcript."""
        if 0 <= row_index < len(self.messages.rows):
            tools = list(self.messages.rows[row_index].get("tools") or [])
            if 0 <= tool_index < len(tools):
                tools[tool_index] = {**tools[tool_index], "open": not tools[tool_index]["open"]}
                self.messages.update(row_index, tools=tools)

    @Slot(str)
    def openChat(self, conversation_id: str) -> None:
        home = self._home_of(conversation_id)
        if home:  # a project's session opens in «Проекты», in its project
            if home != self.projects.currentId:
                self.projects.openProject(home)
            self._project_chat = True
            self.setMode("projects")
        elif self._mode == "projects":  # an ordinary chat opened from «Проекты» (a card, the palette): «Чаты»
            self._project_chat = False
            self.setMode("chats")
        if conversation_id != self.current:
            self.current = conversation_id
            self.messages.reset([])
            title = next((c["title"] for c in self.conversations if c["id"] == conversation_id), "")
            self._title, self._meta = title or "Новый чат", ""
            self.currentChanged.emit()
            self.headerChanged.emit()
            self.busyChanged.emit()
        self._call(
            lambda: self.client.conversation(conversation_id),
            lambda data: self._show_conversation(conversation_id, data),
        )

    def _show_conversation(self, conversation_id: str, data: dict[str, Any]) -> None:
        if conversation_id != self.current:
            return  # the user moved on while this was loading
        if data.get("busy"):
            self._load_chat_question(conversation_id)  # it may be waiting on a question to the user
        else:
            self._set_chat_question(None)
        self.messages.reset(rows.rows_for(data["messages"], self.models))
        if conversation_id in self._stale_runs and not data.get("busy"):
            self._end_dead_run(conversation_id)  # read before the hub's status came: this says the same
        elif conversation_id in self._interrupted and not data.get("busy"):
            self._mark_interrupted()  # the history has no trace of the cut answer: say what happened
        self._interrupted.discard(conversation_id)
        self._set_chat_projects(data.get("projects") or [], push=False)
        self._set_context(data["messages"], data.get("context"))
        last_model = next((m["model_id"] for m in reversed(data["messages"]) if m["role"] == "assistant"), None)
        self._count = sum(1 for m in data["messages"] if m["role"] in ("user", "assistant"))
        self._spent = sum((m.get("usage") or {}).get("cost_usd", 0) for m in data["messages"])
        self._last_author = rows.model_name(self.models, last_model) if last_model else ""
        self._title = data.get("title") or "Новый чат"
        self._update_meta()
        if data.get("busy"):
            self.busy.add(conversation_id)
        if conversation_id in self.live or data.get("busy"):
            self._ensure_live_row()
            self._render_live()
        if last_model:
            self.chooseModel(last_model)
        self.busyChanged.emit()

    def _update_meta(self) -> None:
        """'4 сообщения · Claude Opus 5.5 · $0.0071' under the title."""
        parts = [f"{self._count} {presenter.plural(self._count, 'сообщение', 'сообщения', 'сообщений')}"]
        if self._last_author:
            parts.append(self._last_author)
        if self._spent:
            parts.append(presenter.money(self._spent))
        self._meta = " · ".join(parts) if self._count else ""
        self.headerChanged.emit()

    @Slot(str, str)
    def renameChat(self, conversation_id: str, title: str) -> None:
        cid = conversation_id or self.current
        if cid:
            self._call(lambda: self.client.rename(cid, title.strip()))

    @Slot(str)
    def askDeleteChat(self, conversation_id: str) -> None:
        cid = conversation_id or self.current
        if not cid:
            return
        title = next((c["title"] for c in self.conversations if c["id"] == cid), "") or "Новый чат"

        def decide(yes: bool) -> None:
            if not yes:
                return

            def done(_r) -> None:
                if self.current == cid:
                    self.newChat()
                self.load_conversations()

            self._call(lambda: self.client.delete_conversation(cid), done)

        self._ask(f"Удалить чат «{title}»?", "Расходы по нему останутся в учёте.", "Удалить", decide)

    @Slot(str, result=str)
    def chatTitle(self, conversation_id: str) -> str:
        cid = conversation_id or self.current or ""
        return next((c["title"] for c in self.conversations if c["id"] == cid), "")

    # ================= the command palette =================

    @Slot(str, result=list)
    def paletteItems(self, query: str) -> list:
        return commands.palette_items(
            query,
            chats=self.chats,
            models=self.model_list,
            budgets=self.budgets,
            has_current=self.current is not None,
            projects=self.projects.items,
        )

    @Slot(str)
    def openUi(self, what: str) -> None:
        """A panel opened from another one (Settings -> Connections)."""
        self.uiRequested.emit(what)

    @Slot(str)
    def runCommand(self, command_id: str) -> None:
        kind, _, value = command_id.partition(":")
        if kind == "chat":
            self.openChat(value)
        elif kind == "model":
            self.chooseModel(value)
        elif kind == "budget":
            self.uiRequested.emit("budgets")
        elif kind == "project":
            self.setMode("projects")
            self.projects.openProject(value)
        elif kind == "action":
            if value == "new":
                self.setMode("chats")
                self.newChat()
            elif value.startswith("mode:"):
                self.setMode(value.removeprefix("mode:"))
            elif value in (
                "feed",
                "approvals",
                "agents",
                "programs",
                "model-settings",
                "memory",
                "agent-access",
                "install-models",
                "setup",
                "check-system",
            ):
                self.uiRequested.emit(value)
            elif value == "search-project":
                self.setMode("projects")
                self.uiRequested.emit("search-project")
            elif value == "new-terminal":
                self.setMode("projects")
                self.terminals.newTerminal()
            elif value == "terminals":
                self.setMode("projects")
                self.terminals.setPanelOpen(not self.terminals.panelOpen)
            elif value == "add-project":
                self.setMode("projects")
                self.uiRequested.emit("add-project")
            elif value in ("models", "budgets", "settings", "rename"):
                self.uiRequested.emit(value)
            elif value in ("key", "connections", "mcp"):  # the MCP servers are a section of Connections
                self.uiRequested.emit("connections")
            elif value == "sync":
                self.syncModels()
            elif value == "unload":
                self.unloadLocal()
            elif value.startswith("theme:"):
                self.setTheme(value.removeprefix("theme:"))
            elif value == "delete":
                self.askDeleteChat("")
            elif value == "stop-hub":
                self.stopHub()

    @Slot()
    def stopHub(self) -> None:
        def decide(yes: bool) -> None:
            if not yes:
                return

            def stop() -> None:
                try:
                    self.client.shutdown()
                except HubError as exc:
                    log.warning("shutdown: %s", exc)

            self.bg.run(stop, lambda _r: QGuiApplication.quit(), lambda _e: QGuiApplication.quit())

        self._ask("Остановить хаб?", "Идущие ответы прервутся. Окно закроется.", "Остановить", decide)

    # ================= sending =================

    @Slot(str)
    def send(self, text: str) -> None:
        if self._chat_ask.get("id") and text.strip():  # the model asked: these words are the answer
            self.answerChat(text)
            return
        if self.busyNow:
            self.stop()
            return
        text = text.strip()
        if not text or self.client is None:
            if text:
                self.draftRestored.emit(text)
            return
        if self._model_id not in self.models:
            self.show_error("Нет доступной модели")
            self.draftRestored.emit(text)
            return
        self._send(text, self._model_id, self._effort, self.current)

    def _send(self, text: str, model_id: str, effort: str, target: str | None, *, confirmed: bool = False) -> None:
        client = self.client
        # @project attaches the project, @project/file also pins the file (D27) - before the message goes out, so
        # the context stored with it already has them
        ids, files = mentions.mentioned(text, self.projects.items)
        added = [pid for pid in ids if pid not in self._chat_projects]
        if added:
            self._chat_projects = [*self._chat_projects, *added]
            self.chatProjectsChanged.emit()
        attach = list(self._chat_projects) if target is None or added else []
        known = self._context_settings["pinned"] if target is not None else []
        wanted = [{"project_id": pid, "path": path} for pid, path in files]
        pinned = [*known, *(p for p in wanted if p not in known)] if wanted else []
        if pinned and target is not None:
            self._context_settings = {**self._context_settings, "pinned": pinned}
            self.contextChanged.emit()
        if added or wanted:
            names = {p["id"]: p["name"] for p in self.projects.items}
            said = [f"проект {names[pid]}" for pid in added] + [f"файл {path}" for _pid, path in files]
            self.flash("В контексте чата: " + ", ".join(said))

        refusal = (self._context_settings.get("refusal_vector") or "") if target is None else ""
        mode = (self._context_settings.get("mode") or "ask") if target is None else "ask"

        home = self._new_home if target is None and self._project_chat else ""

        def send() -> str:
            conversation_id = target or client.create_conversation(home_project=home or None)
            try:
                if attach:  # a new chat takes the projects chosen before its first message
                    client.set_chat_projects(conversation_id, attach)
                if pinned or refusal or mode != "ask":  # and its refusal vector choice, its rights
                    client.set_context(conversation_id, **({"pinned": pinned} if pinned else {}),
                                       **({"refusal_vector": refusal} if refusal else {}),
                                       **({"mode": mode} if mode != "ask" else {}))  # fmt: skip
                client.send(conversation_id, text, model_id, effort, confirm_over_budget=confirmed)
            except HubError as exc:
                exc.conversation_id = conversation_id  # a chat made for this message: a retry goes there
                raise
            return conversation_id

        def give_up(made: str | None) -> None:
            if made:  # the chat made for a message that did not go out stays empty: remove it
                self.bg.run(lambda: client.delete_conversation(made))
            self.draftRestored.emit(text)

        def failed(error: Exception) -> None:
            made = getattr(error, "conversation_id", None) if target is None else None
            if getattr(error, "code", None) == "budget_exceeded":
                over = error.data.get("budgets", [])
                if error.data.get("can_confirm"):

                    def decide(yes: bool) -> None:
                        if yes:
                            self._send(text, model_id, effort, made or target, confirmed=True)
                        else:
                            give_up(made)

                    self._ask("Бюджет исчерпан", presenter.budget_question(over), "Отправить", decide)
                    return
                self.show_error("Бюджет исчерпан, платные запросы запрещены: " + presenter.budget_line(over[0]))
            else:
                self.show_error(str(error))
            give_up(made)

        self.bg.run(send, self._sent, failed)

    def _sent(self, conversation_id: str) -> None:
        self.busy.add(conversation_id)
        if self.current is None:  # a new chat was made for this message
            self.current = conversation_id
            if self._new_home and self._project_chat:  # a project's session: known as such before the list comes
                self.conversations = [{"id": conversation_id, "kind": "chat", "title": "", "home_project":
                                       self._new_home}, *self.conversations]  # fmt: skip
            self.load_conversations()
            self.openChat(conversation_id)
        self.busyChanged.emit()
        self.chatsChanged.emit()

    @Slot()
    def stop(self) -> None:
        conversation_id = self.current
        if conversation_id:
            self._call(lambda: self.client.cancel(conversation_id))

    def _ask(self, title: str, text: str, confirm: str, decide) -> None:
        self._question = {"title": title, "text": text, "confirm": confirm}
        self._pending = decide
        self.questionChanged.emit()

    @Slot(bool)
    def answer(self, yes: bool) -> None:
        decide, self._pending = self._pending, None
        self._question = {}
        self.questionChanged.emit()
        if decide is not None:
            decide(yes)

    # ================= events from the hub =================

    @Slot(dict)
    def on_event(self, event: dict[str, Any]) -> None:
        self.feed.on_event(event)
        kind = event.get("type", "")
        payload = event.get("payload") or {}
        cid = payload.get("conversation_id")
        if kind == "chat.run_started":
            self.busy.add(cid)
            self.live[cid] = LiveRun(payload.get("model_id", ""))
            self._run_models[cid] = payload.get("model_id", "")
            if cid == self.current:
                self._ensure_live_row()
            self.busyChanged.emit()
            self.chatsChanged.emit()
        elif kind == "chat.loading":
            run = self.live.setdefault(cid, LiveRun(self._run_models.get(cid, "")))
            lines = [line for line in payload.get("text", "").splitlines() if line.strip()]
            run.loading = lines[-1].strip() if lines else run.loading
            run.clock_from = time.monotonic()  # the thinking clock starts once the model is in VRAM
            self._schedule_render(cid)
        elif kind == "chat.delta":
            # after a tool step the run goes on with the same model: its next answer is that model's, too
            run = self.live.setdefault(cid, LiveRun(self._run_models.get(cid, "")))
            run.loading = ""
            if payload.get("kind") == "reasoning":
                run.reasoning += payload.get("text", "")
            else:
                run.answer_at = run.answer_at or time.monotonic()
                run.text += payload.get("text", "")
            self._schedule_render(cid)
        elif kind == "chat.message":
            message = payload["message"]
            run = self.live.pop(cid, None) if message["role"] == "assistant" else None
            if cid == self.current:
                self._message_arrived(message, run)
        elif kind == "chat.done":
            self._run_ended(cid)
            self.session.add(payload.get("usage"), payload.get("cost_usd"))
            if cid == self.current:
                self._spent += payload.get("cost_usd") or 0
                self._update_meta()
                index = self.messages.find(f"m{payload.get('message_id')}")
                if index >= 0:
                    stats = payload.get("stats") or {}
                    usage = {
                        **(payload.get("usage") or {}),
                        "cancelled": payload.get("cancelled"),
                        "stop_reason": payload.get("stop_reason"),
                    }
                    changes = {"footer": presenter.answer_meta(usage, stats=stats, cost=payload.get("cost_usd"))}
                    think = self.messages.rows[index].get("thinking")
                    if think and "think_s" in stats:
                        changes["thinking"] = {**think, "seconds": presenter.seconds(stats["think_s"])}
                    self.messages.update(index, **changes)
            self.sessionChanged.emit()
            self.refresh_usage()
            self.load_conversations()  # the chat moved to the top of its day
        elif kind == "chat.fallback":  # the model failed: the spare one answers now (D39)
            if payload.get("usage"):  # the declined request still cost its tokens
                self.session.add(payload["usage"], payload.get("cost_usd"))
                self.sessionChanged.emit()
            self._run_models[cid] = payload.get("model_id", "")
            if cid in self.live:
                self.live[cid].model_id = payload.get("model_id", "")
            if payload.get("planned"):  # an autonomous run: the planner made the plan, the run's model goes on
                self.flash(f"План готов — дальше работает «{payload.get('name', '')}»")
            else:
                self.flash(rows.fallback_note({"from_name": payload.get("from_name"), "reason": payload.get("reason")})
                           .replace("Ответила запасная модель", f"Отвечает запасная «{payload.get('name', '')}»"),
                           ms=10000)  # fmt: skip
        elif kind == "chat.error":
            self._run_ended(cid)
            if cid == self.current:
                self._drop_live_row()
                detail = payload.get("detail", "ошибка")
                if payload.get("refusal") and payload.get("cost_usd"):
                    detail += f" Потрачено: {presenter.money(payload['cost_usd'])}."
                self._errors += 1
                self.messages.append(rows.error_row(f"e{self._errors}", detail))
            if payload.get("usage"):
                self.session.add(payload.get("usage"), payload.get("cost_usd"))
                self.sessionChanged.emit()
                self.refresh_usage()
        elif kind == "chat.context":
            if cid == self.current:
                self.loadContext()
        elif kind == "chat.projects":
            if cid == self.current:
                self._set_chat_projects(payload.get("projects") or [], push=False)
        elif kind == "chat.titled":
            if cid == self.current:
                self._title = payload.get("title", "") or "Новый чат"
                self.headerChanged.emit()
            self.load_conversations()
        elif kind in ("budget.warning", "budget.exceeded"):
            text = presenter.budget_event_text(kind.removeprefix("budget."), payload["budget"])
            self.flash(text, error=True, ms=20000)
            self._call(lambda: self.client.budgets(), self._set_budgets)
        elif kind == "budget.changed":
            self._call(lambda: self.client.budgets(), self._set_budgets)
        elif kind in ("models.synced", "secrets.changed"):
            self.load_models()
            if kind == "secrets.changed":
                self.connections.load()
        elif kind.startswith(("connections.", "telegram.")):
            self.connections.on_event(kind, payload)
        elif kind == "system.status":
            self._show_status(payload)
        elif kind.startswith(("project.", "processes.", "card.", "note.", "link")):
            self.projects.on_event(kind, payload)
        elif kind.startswith("terminal."):
            self.terminals.on_event(kind, payload)
        elif kind.startswith("approval."):
            self.approvals.on_event(kind, payload)
            self.agents.on_event(kind, payload)  # a run waits for the user, or goes on
        elif kind.startswith(("run.", "agents.")):
            self.agents.on_event(kind, payload)
            if kind in ("run.question", "run.answered") and not self.autonomy_view.is_task(payload.get("run_id") or ""):
                self._chat_question_event(kind, payload)  # a chat's model asks, not an agent's task
            elif kind.startswith("run."):
                self.autonomy_view.on_event(kind, payload)
        elif kind.startswith(("programs.", "program.")):
            self.programs.on_event(kind, payload)
        elif kind.startswith("install."):
            self.install_view.on_event(kind, payload)
        elif kind.startswith("setup."):
            self.setup_view.on_event(kind, payload)
        elif kind.startswith("sandbox."):
            self.sandbox_view.on_event(kind, payload)
        elif kind == "agent.keys":
            self.agent_keys.on_event(kind, payload)
        elif kind.startswith("mail."):
            self.mail_view.on_event(kind, payload)
        elif kind.startswith("schedule."):
            self.schedules_view.on_event(kind, payload)
        elif kind.startswith("computer."):
            self.computer_view.on_event(kind, payload)
        elif kind.startswith("mcp."):
            self.mcp_view.on_event(kind, payload)
        elif kind.startswith("memory."):
            self.memory_view.on_event(kind, payload)
        elif kind in ("model.settings", "llama.config"):
            self.model_setup.on_event(kind, payload)
        elif kind == "claude_code.limit":
            self.flash(presenter.claude_code_limit(payload.get("info") or {}), error=True, ms=20000)
        elif kind == "hub.stopping":
            self._set_hub("stopped")
            for run_cid in list(self.live):  # an answer does not outlive the hub; nothing else would end it
                self._end_dead_run(run_cid)

    def _chat_question_event(self, kind: str, payload: dict[str, Any]) -> None:
        cid = payload.get("run_id") or ""
        if kind == "run.answered":
            if cid == self.current and self._chat_ask.get("id") == payload.get("id"):
                self._set_chat_question(None)
            return
        if cid == self.current:
            self._set_chat_question({"id": payload.get("id"), "question": payload.get("question", ""),
                                     "options": payload.get("options") or []})  # fmt: skip
        else:
            title = next((c["title"] for c in self.conversations if c["id"] == cid), "чат")
            self.flash(f"Модель спрашивает в «{title}»", ms=10000)

    def _end_dead_run(self, conversation_id: str) -> None:
        """A run the hub no longer has (it stopped or restarted mid-answer): what was shown stays, marked cut. The
        chat is read again after a break, so the mark waits for that, too (`_interrupted`)."""
        self._run_ended(conversation_id)
        self._stale_runs.discard(conversation_id)
        self._interrupted.add(conversation_id)
        if conversation_id == self.current:
            self._drop_live_row()
            self._mark_interrupted()

    def _mark_interrupted(self) -> None:
        self._errors += 1
        self.messages.append(rows.error_row(f"e{self._errors}", "Ответ прервался: хаб остановился."))

    def _run_ended(self, conversation_id: str | None) -> None:
        self.busy.discard(conversation_id)
        self.live.pop(conversation_id, None)
        if conversation_id == self.current and self._chat_ask:
            self._set_chat_question(None)  # the answer ended: its question went with it
        self._run_models.pop(conversation_id, None)
        if conversation_id == self.current:
            self._think_timer.stop()
        self.busyChanged.emit()
        self.chatsChanged.emit()

    def _message_arrived(self, message: dict[str, Any], run: LiveRun | None) -> None:
        if self.messages.find(f"m{message['id']}") >= 0:
            return
        if message["role"] == "tool":  # results join the answers that asked for them
            for index, tools in rows.answered(self.messages.rows, message["content"]).items():
                self.messages.update(index, tools=tools)
            return
        self._count += 1
        if message["role"] == "assistant":
            self._last_author = rows.model_name(self.models, message.get("model_id"))
        self._update_meta()
        live = self.messages.find("live")
        if message["role"] == "assistant" and live >= 0:
            self._think_timer.stop()
            row = rows.assistant_row(message, self.models)
            before = self.messages.rows[live].get("thinking") or {}
            if row["thinking"]:
                elapsed = run.think_elapsed if run else None
                row["thinking"] = {
                    **row["thinking"],
                    "seconds": presenter.seconds(elapsed) if elapsed is not None else "",
                    "open": bool(before.get("open")),
                }
            self.messages.update(live, **row)
            return
        self.messages.append(
            rows.user_row(message) if message["role"] == "user" else rows.assistant_row(message, self.models)
        )

    def _ensure_live_row(self) -> None:
        if self.messages.find("live") < 0:
            run = self.live.get(self.current)
            self.messages.append(rows.live_row(run.model_id if run else "", self.models))

    def _drop_live_row(self) -> None:
        self._think_timer.stop()
        index = self.messages.find("live")
        if index < 0:
            return
        if self.messages.rows[index]["segments"]:
            self._errors += 1
            self.messages.update(index, key=f"cut{self._errors}", streaming=False, notice="")
        else:
            self.messages.remove(index)

    def _schedule_render(self, conversation_id: str | None) -> None:
        if conversation_id == self.current:
            self._ensure_live_row()
            if not self._render_timer.isActive():
                self._render_timer.start()

    def _render_live(self) -> None:
        started = time.perf_counter()
        self._render_live_row()
        spent_ms = (time.perf_counter() - started) * 1000
        interval = min(RENDER_INTERVAL_MAX_MS, max(RENDER_INTERVAL_MS, round(spent_ms * RENDER_SHARE)))
        self._render_timer.setInterval(interval)

    def _render_live_row(self) -> None:
        index = self.messages.find("live")
        if index < 0:
            self._think_timer.stop()
            return
        run = self.live.get(self.current)
        if run is None:
            self.messages.update(index, notice="ответ идёт…")
            return
        before = self.messages.rows[index].get("thinking") or {}
        changes: dict[str, Any] = {
            "author": rows.model_name(self.models, run.model_id),
            "local": rows.is_local(self.models, run.model_id),
            "notice": f"Загрузка модели: {run.loading}" if run.loading else "",
        }
        if run.reasoning:
            changes["thinking"] = rows.thinking(
                run.reasoning, run.think_elapsed, running=run.thinking, open_=bool(before.get("open"))
            )
        if run.text != self.messages.rows[index]["markdown"]:
            changes["markdown"] = run.text
            changes["segments"] = rows.segments(run.text)
        self.messages.update(index, **changes)
        if run.thinking and not self._think_timer.isActive():
            self._think_timer.start()
        elif not run.thinking:
            self._think_timer.stop()

    # ================= small commands from the view =================

    @Slot(int)
    def toggleThinking(self, index: int) -> None:
        if 0 <= index < len(self.messages.rows):
            think = self.messages.rows[index].get("thinking")
            if think:
                self.messages.update(index, thinking={**think, "open": not think.get("open")})

    @Slot(str)
    def copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.flash("Скопировано", ms=2000)

    @Slot(str)
    def openLink(self, url: str) -> None:
        if url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))

    @Slot(str)
    def setMode(self, mode: str) -> None:
        """Chats or projects in the main area."""
        if mode in MODES and mode != self._mode:
            self._mode = mode
            self.settings.setValue("ui/mode", mode)
            self.modeChanged.emit()
            if mode == "chats" and (self._home_of(self.current) or self._new_home):
                self.newChat()  # «Чаты» never shows a project's session

    @Slot(str)
    def setTheme(self, mode: str) -> None:
        self.settings.setValue("ui/theme", mode)
        self.theme.setMode(mode)

    def _restyle(self) -> None:
        """A theme switch: the messages' HTML carries the old colours, render it again."""
        for index, row in enumerate(list(self.messages.rows)):
            self.messages.update(index, **rows.restyled(row))

    # ================= flash messages =================

    def flash(self, text: str, *, error: bool = False, ms: int = FLASH_MS) -> None:
        self._set_flash(text, error)
        self._flash_timer.start(ms)

    def _set_flash(self, text: str, error: bool = False) -> None:
        self._flash, self._flash_error = text, error
        self.flashChanged.emit()

    @Slot(str)
    def notice(self, text: str) -> None:
        self.flash(text)

    @Slot(str)
    def show_error(self, message: str) -> None:
        log.warning("%s", message)
        self.flash(message, error=True, ms=10000)
