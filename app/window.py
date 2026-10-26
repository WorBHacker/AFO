"""The main window: chats on the left, the conversation and the composer on the right, a 26px status bar below.

Everything goes through the hub (D2, D31): REST calls run off the GUI thread (Background), answers stream in
as events (EventThread). Streamed deltas are kept for every chat, so switching chats mid-answer loses nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from common.paths import DataPaths

from . import palette, presenter, theme
from .events import EventThread
from .hub_client import HubClient, HubError, connect
from .tasks import Background
from .widgets import Composer, MessageWidget, Transcript

log = logging.getLogger("app.window")

ANTHROPIC_KEY = "anthropic_api_key"
RENDER_INTERVAL_MS = 80  # streamed text is re-rendered at most ~12 times a second
THINK_TICK_MS = 100  # the thinking timer while a model reasons
FLASH_MS = 6000
VRAM_WARN = 0.98  # a loaded model fills VRAM by design; only the last couple of percent is a warning
HUB_STATES = {  # state -> (palette colour, words)
    "connecting": ("muted", "подключение…"),
    "up": ("ok", "подключён"),
    "lost": ("warn", "нет связи, переподключение…"),
    "down": ("error", "недоступен"),
    "stopped": ("error", "остановлен"),
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


class MainWindow(QMainWindow):
    def __init__(self, paths: DataPaths, settings: QSettings | None = None):
        super().__init__()
        self.paths = paths
        self.settings = settings or QSettings(str(paths.root / "app.ini"), QSettings.Format.IniFormat)
        self.bg = Background(self)
        self.bg.error.connect(self.show_error)
        self.client: HubClient | None = None
        self.events: EventThread | None = None
        self.models: dict[str, dict[str, Any]] = {}
        self.titles: dict[str, str] = {}
        self.busy: set[str] = set()
        self.live: dict[str, LiveRun] = {}
        self.current: str | None = None
        self.message_widgets: dict[int, MessageWidget] = {}
        self.live_widget: MessageWidget | None = None
        self.session = presenter.Session()
        self.totals: dict[str, Any] | None = None
        self.budgets: list[dict[str, Any]] = []
        self._link: bool | None = None  # events connection: never up yet / up / lost
        self._hub_state = "connecting"
        self.theme_mode = self.settings.value("ui/theme", "system")
        app = QApplication.instance()
        theme.apply(app, self.theme_mode)
        app.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        self._render_timer = self._timer(RENDER_INTERVAL_MS, self._render_live, single=True)
        self._think_timer = self._timer(THINK_TICK_MS, self._render_live)
        self._flash_timer = self._timer(FLASH_MS, lambda: self.flash_label.clear(), single=True)

        self.setWindowTitle("LocalChat")
        self._build()
        self._build_status_bar()
        self._restore_layout()

    def _timer(self, interval: int, slot, *, single: bool = False) -> QTimer:
        timer = QTimer(self)
        timer.setInterval(interval)
        timer.setSingleShot(single)
        timer.timeout.connect(slot)
        return timer

    # --- layout ---

    def _build(self) -> None:
        hub_menu = self.menuBar().addMenu("Хаб")
        self._add_action(hub_menu, "Новый чат", self.new_chat, QKeySequence.StandardKey.New)
        hub_menu.addSeparator()
        self._add_action(hub_menu, "Обновить список моделей", self.sync_models)
        self._add_action(hub_menu, "Выгрузить локальную модель из видеопамяти", self.unload_local)
        self._add_action(hub_menu, "Ключ Anthropic…", self.set_anthropic_key)
        self._add_action(hub_menu, "Удалить ключ Anthropic", self.delete_anthropic_key)
        self._add_action(hub_menu, "Бюджеты…", self.open_budgets)
        hub_menu.addSeparator()
        self._add_action(hub_menu, "Подключиться заново", self.connect_hub)
        self._add_action(hub_menu, "Остановить хаб и выйти", self.stop_hub_and_quit)

        view_menu = self.menuBar().addMenu("Вид")
        themes = view_menu.addMenu("Тема")
        group = QActionGroup(self)
        self.theme_actions: dict[str, QAction] = {}
        for mode, label in theme.MODES:
            action = QAction(label, self, checkable=True)
            action.setChecked(mode == self.theme_mode)
            action.triggered.connect(lambda _checked=False, m=mode: self.set_theme(m))
            group.addAction(action)
            themes.addAction(action)
            self.theme_actions[mode] = action

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 10, 8, 8)
        side.setSpacing(8)
        self.new_button = QPushButton("+  Новый чат")
        self.new_button.setObjectName("newChat")
        self.new_button.clicked.connect(self.new_chat)
        self.chat_list = QListWidget()
        self.chat_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.chat_list.verticalScrollBar().setSingleStep(12)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list.setTextElideMode(Qt.TextElideMode.ElideRight)  # long titles end in "…", full one in the tooltip
        self.chat_list.currentItemChanged.connect(self._on_chat_selected)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._chat_menu)
        side.addWidget(self.new_button)
        side.addWidget(self.chat_list)

        main = QWidget()
        main.setObjectName("main")
        column = QVBoxLayout(main)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.transcript = Transcript()
        column.addWidget(self.transcript, 1)
        column.addWidget(self._composer_card())

        self.splitter = QSplitter()
        self.splitter.addWidget(sidebar)
        self.splitter.addWidget(main)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([250, 850])
        self.setCentralWidget(self.splitter)

    def _composer_card(self) -> QWidget:
        """The input and its controls in one card: text above, model / effort / send below."""
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(20, 4, 20, 14)
        card = QFrame()
        card.setObjectName("composerCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(10, 8, 10, 8)
        inner.setSpacing(6)
        self.composer = Composer()
        self.composer.submitted.connect(self.on_send_clicked)
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(260)
        self.model_box.setToolTip("Модель для следующего сообщения")
        self.model_box.currentIndexChanged.connect(self._on_model_chosen)
        self.effort_box = QComboBox()
        for value, label in presenter.EFFORTS:
            self.effort_box.addItem(f"Глубина: {label}", value)
        self.effort_box.setToolTip("Глубина размышлений модели (effort)")
        for box in (self.model_box, self.effort_box):
            box.view().setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.send_button = QPushButton("Отправить")
        self.send_button.setObjectName("sendButton")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.on_send_clicked)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.model_box, 1)
        controls.addWidget(self.effort_box)
        controls.addWidget(self.send_button)
        inner.addWidget(self.composer)
        inner.addLayout(controls)
        outer.addWidget(card)
        return holder

    def _build_status_bar(self) -> None:
        """Hub and port · active model · this session's tokens and cost · flash messages · machine."""
        bar = self.statusBar()
        bar.setFixedHeight(palette.STATUS_BAR)
        bar.setSizeGripEnabled(False)
        bar.setContentsMargins(6, 0, 6, 0)
        self.hub_label = QLabel()
        self.model_label = QLabel()
        self.session_label = QLabel(self.session.text())
        self.flash_label = QLabel()
        self.flash_label.setObjectName("flash")
        for widget in (self.hub_label, self._separator(), self.model_label, self._separator(), self.session_label):
            bar.addWidget(widget)
        bar.addWidget(self.flash_label, 1)

        self.local_label = QLabel()
        self.vram_bar, self.vram_text = self._meter()
        self.commit_bar, self.commit_text = self._meter()
        self.gpu_label = QLabel()
        right = [
            self.local_label,
            self._separator(),
            QLabel("VRAM"),
            self.vram_bar,
            self.vram_text,
            self._separator(),
            QLabel("Память"),
            self.commit_bar,
            self.commit_text,
            self._separator(),
            self.gpu_label,
        ]
        for widget in right:
            bar.addPermanentWidget(widget)
        self._set_hub_state("connecting")

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setObjectName("statusSep")
        line.setFixedSize(1, 12)
        return line

    def _meter(self) -> tuple[QProgressBar, QLabel]:
        meter = QProgressBar()
        meter.setObjectName("meter")
        meter.setRange(0, 1000)
        meter.setTextVisible(False)
        return meter, QLabel("—")

    def _add_action(self, menu: QMenu, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _restore_layout(self) -> None:
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1180, 780)
        splitter = self.settings.value("window/splitter")
        if splitter is not None:
            self.splitter.restoreState(splitter)
        effort = self.settings.value("chat/effort", "medium")
        index = self.effort_box.findData(effort)
        self.effort_box.setCurrentIndex(index if index >= 0 else 1)

    # --- theme ---

    def set_theme(self, mode: str) -> None:
        """'system', 'light' or 'dark'; switches at once and is remembered."""
        self.theme_mode = mode
        self.settings.setValue("ui/theme", mode)
        self.theme_actions[mode].setChecked(True)
        self._apply_theme()

    def _system_scheme_changed(self, _scheme) -> None:
        if self.theme_mode == "system":
            self._apply_theme()

    def _apply_theme(self) -> None:
        theme.apply(QApplication.instance(), self.theme_mode)
        for widget in self.transcript.messages():  # their HTML carries the old colours
            widget.restyle()
        self._set_hub_state(self._hub_state)

    # --- the status bar ---

    def _set_hub_state(self, state: str) -> None:
        self._hub_state = state
        colour_name, text = HUB_STATES[state]
        colour = getattr(palette.current(), colour_name)
        port = presenter.hub_port(self.client.base_url if self.client else None)
        self.hub_label.setText(f'<span style="color:{colour};">●</span>&nbsp;Хаб {port}'.rstrip())
        where = self.client.base_url if self.client else "адрес ещё не известен"
        self.hub_label.setToolTip(f"Хаб: {text}\n{where}")

    def _show_active_model(self) -> None:
        model = self.models.get(self.model_box.currentData() or "")
        if model is None:
            self.model_label.setText("Модель: —")
            self.model_label.setToolTip("")
            return
        self.model_label.setText(f"Модель: {model['name']}")
        self.model_label.setToolTip(presenter.model_label(model))

    def _show_session(self) -> None:
        self.session_label.setText(self.session.text())
        tooltip = self.session.tooltip(self.totals)
        if self.budgets:
            lines = [presenter.budget_line(b) for b in self.budgets]
            tooltip += "\n\nБюджеты:\n" + "\n".join(lines)
        self.session_label.setToolTip(tooltip)
        self.session_label.setProperty("warn", presenter.budgets_state(self.budgets) != "ok")
        self.session_label.style().polish(self.session_label)

    def flash(self, text: str, *, error: bool = False, ms: int = FLASH_MS) -> None:
        """A short message in the status bar that clears itself; the rest of the bar stays in place."""
        self.flash_label.setText(text)
        self.flash_label.setToolTip(text)
        self.flash_label.setProperty("warn", error)
        self.flash_label.style().polish(self.flash_label)
        self._flash_timer.start(ms)

    # --- connecting ---

    def connect_hub(self) -> None:
        self._set_hub_state("connecting")
        self.flash("Подключение к хабу (если он не запущен, приложение запускает его)…")
        self.bg.run(lambda: connect(self.paths), self.attach, self._connect_failed)

    def _connect_failed(self, error: Exception) -> None:
        self._set_hub_state("down")
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
        self._set_hub_state("up")
        self.flash_label.clear()
        self.reload_all()

    def _on_connected(self, connected: bool) -> None:
        self._set_hub_state("up" if connected else "lost")
        if connected and self._link is False:
            self.reload_all()  # back after a break: deltas are not replayed, so fetch what happened
        self._link = connected

    def reload_all(self) -> None:
        self.load_models()
        self.load_conversations()
        self.refresh_status()
        self.refresh_usage()
        if self.current:
            self.open_conversation(self.current)

    def _call(self, fn, on_done=None, on_error=None) -> None:
        if self.client is None:
            self.show_error("Хаб ещё не подключён")
            return
        self.bg.run(fn, on_done, on_error)

    # --- models ---

    def load_models(self) -> None:
        self._call(lambda: self.client.models(), self._set_models)

    def _set_models(self, models: list[dict[str, Any]]) -> None:
        wanted = self.model_box.currentData() or self.settings.value("chat/model")
        self.models = {m["id"]: m for m in models}
        self.model_box.blockSignals(True)
        self.model_box.clear()
        ordered = presenter.sorted_models(models)
        for i, model in enumerate(ordered):
            if i and model.get("locality") != ordered[i - 1].get("locality"):
                self.model_box.insertSeparator(self.model_box.count())
            self.model_box.addItem(presenter.model_label(model), model["id"])
        if not models:
            self.model_box.addItem("Нет моделей: запустите llama-swap или задайте ключ Anthropic", None)
        index = self.model_box.findData(wanted) if wanted else -1  # findData(None) would match a separator
        self.model_box.setCurrentIndex(index if index >= 0 else 0)
        self.model_box.blockSignals(False)
        self._show_active_model()

    def _on_model_chosen(self, _index: int) -> None:
        if self.model_box.currentData():
            self.settings.setValue("chat/model", self.model_box.currentData())
        self._show_active_model()

    def _choose_model(self, model_id: str | None) -> None:
        index = self.model_box.findData(model_id) if model_id else -1
        if index >= 0:
            self.model_box.setCurrentIndex(index)

    def sync_models(self) -> None:
        def done(result: dict) -> None:
            self.load_models()
            if result.get("errors"):
                self.flash(f"Список моделей обновлён, ошибки: {result['errors']}", error=True)
            else:
                self.flash("Список моделей обновлён")

        self._call(lambda: self.client.sync_models(), done)

    def unload_local(self) -> None:
        self._call(lambda: self.client.unload_local(), lambda _r: self.flash("Локальная модель выгружена"))

    def set_anthropic_key(self) -> None:
        value, ok = QInputDialog.getText(
            self,
            "Ключ Anthropic",
            "API-ключ Claude. Хранится в диспетчере учётных данных Windows, в файлы не попадает:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not value.strip():
            return

        def done(result: dict) -> None:
            self.load_models()
            catalog = result.get("catalog", {})
            if catalog.get("errors"):
                self.show_error(f"Ключ сохранён, но список моделей не получен: {catalog['errors']}")
            else:
                self.flash("Ключ сохранён, модели Claude добавлены")

        self._call(lambda: self.client.set_secret(ANTHROPIC_KEY, value.strip()), done)

    def delete_anthropic_key(self) -> None:
        if self._confirm("Удалить ключ Anthropic? Модели Claude пропадут из списка."):
            self._call(lambda: self.client.delete_secret(ANTHROPIC_KEY), lambda _r: self.load_models())

    # --- the machine ---

    def refresh_status(self) -> None:
        self._call(lambda: self.client.status(), self._show_status)

    def _show_status(self, status: dict[str, Any]) -> None:
        details = presenter.status_text(status)
        self.local_label.setText(presenter.local_model(status))
        for bar, text, meter, warn_at in (
            (self.vram_bar, self.vram_text, presenter.vram_meter(status), VRAM_WARN),
            (self.commit_bar, self.commit_text, presenter.commit_meter(status), None),
        ):
            ratio, label = meter if meter else (0.0, "—")
            warn = presenter.commit_is_tight(status) if warn_at is None else ratio >= warn_at
            bar.setValue(round(ratio * 1000))
            text.setText(label)
            for widget in (bar, text):
                widget.setProperty("warn", warn)
                widget.style().polish(widget)
        self.gpu_label.setText(presenter.gpu_load(status))
        tight = presenter.commit_is_tight(status)
        hint = "\nПамять Windows почти исчерпана (ОЗУ + файл подкачки): большая модель может не загрузиться"
        for widget in (self.local_label, self.vram_bar, self.vram_text, self.commit_bar, self.commit_text):
            widget.setToolTip(details + (hint if tight else ""))
        if "busy" in status:
            self.busy = set(status["busy"]) | set(self.live)
            self._update_send_button()

    def refresh_usage(self) -> None:
        def show(totals: dict[str, Any]) -> None:
            self.totals = totals
            self._show_session()

        self._call(lambda: self.client.usage(), show)
        self.refresh_budgets()

    def refresh_budgets(self) -> None:
        self._call(lambda: self.client.budgets(), self._set_budgets)

    def _set_budgets(self, budgets: list[dict[str, Any]]) -> None:
        self.budgets = budgets
        self._show_session()

    def open_budgets(self) -> None:
        if self.client is None:
            self.show_error("Хаб ещё не подключён")
            return
        from .budget_dialog import BudgetDialog

        BudgetDialog(self).exec()

    # --- chats ---

    def load_conversations(self) -> None:
        self._call(lambda: self.client.conversations(), self._set_conversations)

    def _set_conversations(self, data: dict[str, Any]) -> None:
        self.busy |= set(data.get("busy", []))
        self.titles = {c["id"]: c["title"] for c in data["conversations"]}
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for conversation in data["conversations"]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, conversation["id"])
            self.chat_list.addItem(item)
            self._label_item(item)
            if conversation["id"] == self.current:
                self.chat_list.setCurrentItem(item)
        self.chat_list.blockSignals(False)

    def _item(self, conversation_id: str) -> QListWidgetItem | None:
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == conversation_id:
                return item
        return None

    def _label_item(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.ItemDataRole.UserRole)
        title = self.titles.get(cid) or "Новый чат"
        item.setText(("● " if cid in self.busy else "") + title)
        item.setToolTip(title)

    def _relabel(self, conversation_id: str | None) -> None:
        item = self._item(conversation_id) if conversation_id else None
        if item is not None:
            self._label_item(item)

    def _on_chat_selected(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is not None:
            self.open_conversation(item.data(Qt.ItemDataRole.UserRole))

    def new_chat(self) -> None:
        self.current = None
        self.chat_list.blockSignals(True)
        self.chat_list.clearSelection()
        self.chat_list.setCurrentItem(None)
        self.chat_list.blockSignals(False)
        self._clear_transcript()
        self._update_send_button()
        self.composer.setFocus()

    def open_conversation(self, conversation_id: str) -> None:
        if conversation_id != self.current:
            self.current = conversation_id
            self._clear_transcript()
        self._call(
            lambda: self.client.conversation(conversation_id),
            lambda data: self._show_conversation(conversation_id, data),
        )

    def _clear_transcript(self) -> None:
        self.transcript.clear()
        self.message_widgets = {}
        self.live_widget = None
        self._think_timer.stop()

    def _show_conversation(self, conversation_id: str, data: dict[str, Any]) -> None:
        if conversation_id != self.current:
            return  # the user moved on while this was loading
        self._clear_transcript()
        last_model = None
        for message in data["messages"]:
            self._add_message(message)
            if message["role"] == "assistant":
                last_model = message.get("model_id")
        if data.get("busy"):
            self.busy.add(conversation_id)
        if conversation_id in self.live or data.get("busy"):
            self._ensure_live_widget()
            self._render_live()
        self._choose_model(last_model)
        self._update_send_button()

    def _add_message(self, message: dict[str, Any]) -> MessageWidget:
        text, reasoning = presenter.split_blocks(message["content"])
        if message["role"] == "user":
            widget = MessageWidget("user", "Вы")
            widget.set_plain(text)
        else:
            usage = message.get("usage") or {}
            widget = MessageWidget("assistant", self._model_name(message.get("model_id")))
            if reasoning:
                widget.set_reasoning(reasoning, (usage.get("stats") or {}).get("think_s"))
            widget.set_markdown(text)
            widget.set_footer(presenter.answer_meta(usage))
        self._wire(widget)
        self.message_widgets[message["id"]] = widget
        return self.transcript.add(widget)

    def _wire(self, widget: MessageWidget) -> None:
        widget.copied.connect(lambda: self.flash("Код скопирован", ms=2000))
        widget.link_activated.connect(self._open_link)

    def _open_link(self, href: str) -> None:
        if href.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(href))

    def _model_name(self, model_id: str | None) -> str:
        if not model_id:
            return "Модель"
        return self.models.get(model_id, {}).get("name") or model_id

    def _chat_menu(self, position) -> None:
        item = self.chat_list.itemAt(position)
        if item is None:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        rename = menu.addAction("Переименовать")
        delete = menu.addAction("Удалить")
        chosen = menu.exec(self.chat_list.mapToGlobal(position))
        if chosen is rename:
            self.rename_chat(cid)
        elif chosen is delete:
            self.delete_chat(cid)

    def rename_chat(self, conversation_id: str) -> None:
        title, ok = QInputDialog.getText(
            self, "Переименовать чат", "Название:", text=self.titles.get(conversation_id, "")
        )
        if ok:
            self._call(lambda: self.client.rename(conversation_id, title.strip()))

    def delete_chat(self, conversation_id: str) -> None:
        title = self.titles.get(conversation_id) or "Новый чат"
        if not self._confirm(f"Удалить чат «{title}»? Расходы по нему останутся в учёте."):
            return

        def done(_result) -> None:
            item = self._item(conversation_id)
            if item is not None:
                self.chat_list.takeItem(self.chat_list.row(item))
            self.titles.pop(conversation_id, None)
            if self.current == conversation_id:
                self.new_chat()

        self._call(lambda: self.client.delete_conversation(conversation_id), done)

    # --- sending ---

    def on_send_clicked(self) -> None:
        if self.current is not None and self.current in self.busy:
            self.cancel_current()
            return
        text = self.composer.toPlainText().strip()
        if not text or self.client is None:
            return
        model_id = self.model_box.currentData()
        if not model_id:
            self.show_error("Нет доступной модели")
            return
        effort = self.effort_box.currentData()
        self.settings.setValue("chat/effort", effort)
        self.composer.clear()
        self._send(text, model_id, effort, self.current)

    def _send(self, text: str, model_id: str, effort: str, target: str | None, *, confirmed: bool = False) -> None:
        client = self.client

        def send() -> str:
            conversation_id = target or client.create_conversation()
            try:
                client.send(conversation_id, text, model_id, effort, confirm_over_budget=confirmed)
            except HubError as exc:
                exc.conversation_id = conversation_id  # a chat made for this message: a retry goes there
                raise
            return conversation_id

        def failed(error: Exception) -> None:
            made = getattr(error, "conversation_id", None) if target is None else None
            if getattr(error, "code", None) == "budget_exceeded":
                over = error.data.get("budgets", [])
                if error.data.get("can_confirm") and self._confirm(presenter.budget_question(over)):
                    self._send(text, model_id, effort, made or target, confirmed=True)
                    return
                if not error.data.get("can_confirm"):
                    self.show_error("Бюджет исчерпан, платные запросы запрещены: " + presenter.budget_line(over[0]))
            else:
                self.show_error(str(error))
            if made:  # the chat made for a message that did not go out stays empty: remove it
                self.bg.run(lambda: client.delete_conversation(made))
            if not self.composer.toPlainText():
                self.composer.setPlainText(text)  # nothing typed is lost

        self.bg.run(send, self._sent, failed)

    def _sent(self, conversation_id: str) -> None:
        self.busy.add(conversation_id)
        if self.current is None:  # a new chat was created for this message
            self.current = conversation_id
            self.load_conversations()
            self.open_conversation(conversation_id)
        self._update_send_button()
        self._relabel(conversation_id)

    def cancel_current(self) -> None:
        conversation_id = self.current
        self._call(lambda: self.client.cancel(conversation_id))

    def _update_send_button(self) -> None:
        stop = self.current is not None and self.current in self.busy
        self.send_button.setText("Стоп" if stop else "Отправить")
        self.send_button.setProperty("stop", stop)
        self.send_button.style().polish(self.send_button)

    # --- events from the hub ---

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        payload = event.get("payload") or {}
        cid = payload.get("conversation_id")
        if kind == "chat.run_started":
            self.busy.add(cid)
            self.live[cid] = LiveRun(payload.get("model_id", ""))
            self._relabel(cid)
            if cid == self.current:
                self._ensure_live_widget()
                self._update_send_button()
        elif kind == "chat.loading":
            run = self.live.setdefault(cid, LiveRun(""))
            lines = [line for line in payload.get("text", "").splitlines() if line.strip()]
            run.loading = lines[-1].strip() if lines else run.loading
            run.clock_from = time.monotonic()  # the thinking clock starts once the model is in VRAM
            self._schedule_render(cid)
        elif kind == "chat.delta":
            run = self.live.setdefault(cid, LiveRun(""))
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
                widget = self.message_widgets.get(payload.get("message_id"))
                if widget is not None:
                    stats = payload.get("stats") or {}
                    usage = {
                        **(payload.get("usage") or {}),
                        "cancelled": payload.get("cancelled"),
                        "stop_reason": payload.get("stop_reason"),
                    }
                    widget.set_footer(presenter.answer_meta(usage, stats=stats, cost=payload.get("cost_usd")))
                    if "think_s" in stats:
                        widget.thinking.set_elapsed(stats["think_s"])
            self._show_session()
            self.refresh_usage()
        elif kind == "chat.error":
            self._run_ended(cid)
            if cid == self.current:
                self._drop_live_widget()
                detail = payload.get("detail", "ошибка")
                if payload.get("refusal") and payload.get("cost_usd"):
                    detail += f" Потрачено: {presenter.money(payload['cost_usd'])}."
                widget = MessageWidget("error", "Ошибка")
                widget.set_plain(detail)
                self.transcript.add(widget)
            if payload.get("usage"):
                self.session.add(payload.get("usage"), payload.get("cost_usd"))
                self._show_session()
                self.refresh_usage()
        elif kind == "chat.titled":
            self.titles[cid] = payload.get("title", "")
            if self._item(cid) is None:
                self.load_conversations()
            else:
                self._relabel(cid)
        elif kind in ("budget.warning", "budget.exceeded"):
            text = presenter.budget_event_text(kind.removeprefix("budget."), payload["budget"])
            self.flash(text, error=True, ms=20000)
            self.refresh_budgets()
        elif kind == "budget.changed":
            self.refresh_budgets()
        elif kind in ("models.synced", "secrets.changed"):
            self.load_models()
        elif kind == "system.status":
            self._show_status(payload)
        elif kind == "hub.stopping":
            self._set_hub_state("stopped")

    def _run_ended(self, conversation_id: str | None) -> None:
        self.busy.discard(conversation_id)
        self.live.pop(conversation_id, None)
        self._relabel(conversation_id)
        if conversation_id == self.current:
            self._think_timer.stop()
            self._update_send_button()

    def _message_arrived(self, message: dict[str, Any], run: LiveRun | None) -> None:
        if message["id"] in self.message_widgets:
            return
        if message["role"] == "assistant" and self.live_widget is not None:
            widget, self.live_widget = self.live_widget, None
            self._think_timer.stop()
            text, reasoning = presenter.split_blocks(message["content"])
            widget.set_header(self._model_name(message.get("model_id")))
            widget.set_notice("")
            if reasoning:
                widget.set_reasoning(reasoning, run.think_elapsed if run else None)
            widget.set_markdown(text)
            self.message_widgets[message["id"]] = widget
            return
        self._add_message(message)

    def _ensure_live_widget(self) -> MessageWidget:
        if self.live_widget is None:
            run = self.live.get(self.current)
            name = self._model_name(run.model_id if run else None)
            self.live_widget = MessageWidget("assistant", f"{name} · отвечает…")
            self._wire(self.live_widget)
            self.transcript.add(self.live_widget)
        return self.live_widget

    def _drop_live_widget(self) -> None:
        self._think_timer.stop()
        if self.live_widget is not None:
            if self.live_widget.has_content:
                self.live_widget.set_header(self.live_widget.header.text().replace(" · отвечает…", ""))
            else:
                self.transcript.remove(self.live_widget)
            self.live_widget = None

    def _schedule_render(self, conversation_id: str | None) -> None:
        if conversation_id == self.current:
            self._ensure_live_widget()
            if not self._render_timer.isActive():
                self._render_timer.start()

    def _render_live(self) -> None:
        run = self.live.get(self.current)
        widget = self.live_widget
        if widget is None:
            self._think_timer.stop()
            return
        if run is None:
            widget.set_notice("ответ идёт…")
            return
        if run.model_id:  # the card may have been made before the run said which model answers
            widget.set_header(f"{self._model_name(run.model_id)} · отвечает…")
        widget.set_notice(f"Загрузка модели: {run.loading}" if run.loading else "")
        if run.reasoning:
            widget.set_reasoning(run.reasoning, run.think_elapsed, running=run.thinking)
        widget.set_markdown(run.text)
        if run.thinking and not self._think_timer.isActive():
            self._think_timer.start()
        elif not run.thinking:
            self._think_timer.stop()

    # --- misc ---

    def show_error(self, message: str) -> None:
        log.warning("%s", message)
        self.flash(message, error=True, ms=10000)

    def _confirm(self, question: str) -> bool:
        answer = QMessageBox.question(self, "LocalChat", question)
        return answer == QMessageBox.StandardButton.Yes

    def stop_hub_and_quit(self) -> None:
        if self.client is None:
            self.close()
            return
        if not self._confirm("Остановить хаб? Идущие ответы прервутся. Приложение закроется."):
            return

        def stop() -> None:
            try:
                self.client.shutdown()
            except HubError as exc:
                log.warning("shutdown: %s", exc)

        self.bg.run(stop, lambda _r: self.close(), lambda _e: self.close())

    def closeEvent(self, event: QCloseEvent) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/splitter", self.splitter.saveState())
        self.settings.sync()
        if self.events is not None:
            self.events.stop()
        self.bg.wait(3000)
        if self.client is not None:
            self.client.close()
        super().closeEvent(event)  # the hub keeps running (D2)
