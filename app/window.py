"""The main window: chats on the left, the conversation and the composer on the right, the machine below.

Everything goes through the hub (D2, D31): REST calls run off the GUI thread (Background), answers stream in
as events (EventThread). Streamed deltas are kept for every chat, so switching chats mid-answer loses nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from common.paths import DataPaths

from . import presenter
from .events import EventThread
from .hub_client import HubClient, HubError, connect
from .tasks import Background
from .widgets import Composer, MessageWidget, Transcript

log = logging.getLogger("app.window")

ANTHROPIC_KEY = "anthropic_api_key"
RENDER_INTERVAL_MS = 80  # streamed text is re-rendered at most ~12 times a second


@dataclass
class LiveRun:
    """An answer being streamed: what arrived so far."""

    model_id: str
    text: str = ""
    reasoning: str = ""
    loading: str = ""


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
        self._link: bool | None = None  # events connection: never up yet / up / lost
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render_live)

        self.setWindowTitle("LocalChat")
        self._build()
        self._restore_layout()

    # --- layout ---

    def _build(self) -> None:
        hub_menu = self.menuBar().addMenu("Хаб")
        self._add_action(hub_menu, "Новый чат", self.new_chat, QKeySequence.StandardKey.New)
        hub_menu.addSeparator()
        self._add_action(hub_menu, "Обновить список моделей", self.sync_models)
        self._add_action(hub_menu, "Выгрузить локальную модель из видеопамяти", self.unload_local)
        self._add_action(hub_menu, "Ключ Anthropic…", self.set_anthropic_key)
        self._add_action(hub_menu, "Удалить ключ Anthropic", self.delete_anthropic_key)
        hub_menu.addSeparator()
        self._add_action(hub_menu, "Подключиться заново", self.connect_hub)
        self._add_action(hub_menu, "Остановить хаб и выйти", self.stop_hub_and_quit)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        self.new_button = QPushButton("+ Новый чат")
        self.new_button.clicked.connect(self.new_chat)
        self.chat_list = QListWidget()
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list.setTextElideMode(Qt.TextElideMode.ElideRight)  # long titles end in "…", full one in the tooltip
        self.chat_list.currentItemChanged.connect(self._on_chat_selected)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._chat_menu)
        left_layout.addWidget(self.new_button)
        left_layout.addWidget(self.chat_list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 8, 8)
        self.transcript = Transcript()
        self.composer = Composer()
        self.composer.submitted.connect(self.on_send_clicked)
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(280)
        self.model_box.currentIndexChanged.connect(self._on_model_chosen)
        self.effort_box = QComboBox()
        for value, label in presenter.EFFORTS:
            self.effort_box.addItem(label, value)
        self.effort_box.setToolTip("Глубина размышлений модели (effort)")
        self.send_button = QPushButton("Отправить")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.on_send_clicked)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Модель:"))
        controls.addWidget(self.model_box, 1)
        controls.addWidget(QLabel("Глубина:"))
        controls.addWidget(self.effort_box)
        controls.addWidget(self.send_button)
        right_layout.addWidget(self.transcript, 1)
        right_layout.addWidget(self.composer)
        right_layout.addLayout(controls)

        self.splitter = QSplitter()
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 760])
        self.setCentralWidget(self.splitter)

        self.conn_label = QLabel("Хаб: не подключён")
        self.status_label = QLabel()
        self.usage_label = QLabel()
        self.statusBar().addWidget(self.conn_label)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.usage_label)

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
            self.resize(1100, 760)
        splitter = self.settings.value("window/splitter")
        if splitter is not None:
            self.splitter.restoreState(splitter)
        effort = self.settings.value("chat/effort", "medium")
        index = self.effort_box.findData(effort)
        self.effort_box.setCurrentIndex(index if index >= 0 else 1)

    # --- connecting ---

    def connect_hub(self) -> None:
        self.conn_label.setText("Хаб: подключение… (если он не запущен, приложение запускает его)")
        self.bg.run(lambda: connect(self.paths), self.attach, self._connect_failed)

    def _connect_failed(self, message: str) -> None:
        self.conn_label.setText("Хаб: недоступен")
        self.show_error(f"Нет связи с хабом: {message}")

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
        self.conn_label.setText("Хаб: подключён")
        self.reload_all()

    def _on_connected(self, connected: bool) -> None:
        if connected:
            self.conn_label.setText("Хаб: подключён")
            if self._link is False:
                self.reload_all()  # back after a break: deltas are not replayed, so fetch what happened
        else:
            self.conn_label.setText("Хаб: нет связи, переподключение…")
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
        index = self.model_box.findData(wanted)
        self.model_box.setCurrentIndex(index if index >= 0 else 0)
        self.model_box.blockSignals(False)
        if not models:
            self.model_box.addItem("Нет моделей: запустите llama-swap или задайте ключ Anthropic", None)

    def _on_model_chosen(self, _index: int) -> None:
        if self.model_box.currentData():
            self.settings.setValue("chat/model", self.model_box.currentData())

    def _choose_model(self, model_id: str | None) -> None:
        index = self.model_box.findData(model_id) if model_id else -1
        if index >= 0:
            self.model_box.setCurrentIndex(index)

    def sync_models(self) -> None:
        def done(result: dict) -> None:
            self.load_models()
            errors = f" Ошибки: {result['errors']}" if result.get("errors") else ""
            self.statusBar().showMessage(f"Список моделей обновлён.{errors}", 8000)

        self._call(lambda: self.client.sync_models(), done)

    def unload_local(self) -> None:
        self._call(
            lambda: self.client.unload_local(),
            lambda _r: self.statusBar().showMessage("Локальная модель выгружена", 5000),
        )

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
                self.statusBar().showMessage("Ключ сохранён, модели Claude добавлены", 8000)

        self._call(lambda: self.client.set_secret(ANTHROPIC_KEY, value.strip()), done)

    def delete_anthropic_key(self) -> None:
        if self._confirm("Удалить ключ Anthropic? Модели Claude пропадут из списка."):
            self._call(lambda: self.client.delete_secret(ANTHROPIC_KEY), lambda _r: self.load_models())

    # --- the machine ---

    def refresh_status(self) -> None:
        self._call(lambda: self.client.status(), self._show_status)

    def _show_status(self, status: dict[str, Any]) -> None:
        self.status_label.setText(presenter.status_text(status))
        tight = presenter.commit_is_tight(status)
        self.status_label.setProperty("warn", tight)
        self.status_label.setToolTip(
            "Память Windows почти исчерпана (ОЗУ + файл подкачки): большая модель может не загрузиться" if tight else ""
        )
        self.status_label.style().polish(self.status_label)
        if "busy" in status:
            self.busy = set(status["busy"]) | set(self.live)
            self._update_send_button()

    def refresh_usage(self) -> None:
        self._call(lambda: self.client.usage(), lambda totals: self.usage_label.setText(presenter.usage_text(totals)))

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
            widget.set_text(text, markdown=False)
        else:
            widget = MessageWidget("assistant", self._model_name(message.get("model_id")))
            widget.set_reasoning(reasoning)
            widget.set_text(text)
            widget.set_footer(presenter.answer_meta(message.get("usage")))
        self._wire(widget)
        self.message_widgets[message["id"]] = widget
        return self.transcript.add(widget)

    def _wire(self, widget: MessageWidget) -> None:
        widget.copied.connect(lambda: self.statusBar().showMessage("Код скопирован", 2000))
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
        target = self.current
        client = self.client

        def send() -> str:
            conversation_id = target or client.create_conversation()
            client.send(conversation_id, text, model_id, effort)
            return conversation_id

        def failed(message: str) -> None:
            if not self.composer.toPlainText():
                self.composer.setPlainText(text)  # nothing typed is lost
            self.show_error(message)

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
            self._schedule_render(cid)
        elif kind == "chat.delta":
            run = self.live.setdefault(cid, LiveRun(""))
            run.loading = ""
            if payload.get("kind") == "reasoning":
                run.reasoning += payload.get("text", "")
            else:
                run.text += payload.get("text", "")
            self._schedule_render(cid)
        elif kind == "chat.message":
            message = payload["message"]
            if message["role"] == "assistant":
                self.live.pop(cid, None)
            if cid == self.current:
                self._message_arrived(message)
        elif kind == "chat.done":
            self._run_ended(cid)
            if cid == self.current:
                widget = self.message_widgets.get(payload.get("message_id"))
                if widget is not None:
                    usage = {
                        **(payload.get("usage") or {}),
                        "cancelled": payload.get("cancelled"),
                        "stop_reason": payload.get("stop_reason"),
                    }
                    widget.set_footer(
                        presenter.answer_meta(usage, stats=payload.get("stats"), cost=payload.get("cost_usd"))
                    )
            self.refresh_usage()
        elif kind == "chat.error":
            self._run_ended(cid)
            if cid == self.current:
                self._drop_live_widget()
                detail = payload.get("detail", "ошибка")
                if payload.get("refusal") and payload.get("cost_usd"):
                    detail += f" Потрачено: {presenter.money(payload['cost_usd'])}."
                widget = MessageWidget("error", "Ошибка")
                widget.set_text(detail, markdown=False)
                self.transcript.add(widget)
            if payload.get("refusal"):
                self.refresh_usage()
        elif kind == "chat.titled":
            self.titles[cid] = payload.get("title", "")
            if self._item(cid) is None:
                self.load_conversations()
            else:
                self._relabel(cid)
        elif kind in ("models.synced", "secrets.changed"):
            self.load_models()
        elif kind == "system.status":
            self._show_status(payload)
        elif kind == "hub.stopping":
            self.conn_label.setText("Хаб: остановлен")

    def _run_ended(self, conversation_id: str | None) -> None:
        self.busy.discard(conversation_id)
        self.live.pop(conversation_id, None)
        self._relabel(conversation_id)
        if conversation_id == self.current:
            self._update_send_button()

    def _message_arrived(self, message: dict[str, Any]) -> None:
        if message["id"] in self.message_widgets:
            return
        if message["role"] == "assistant" and self.live_widget is not None:
            widget, self.live_widget = self.live_widget, None
            text, reasoning = presenter.split_blocks(message["content"])
            widget.set_header(self._model_name(message.get("model_id")))
            widget.set_notice("")
            widget.set_reasoning(reasoning)
            widget.set_text(text)
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
        if self.live_widget is not None:
            if self.live_widget.text:
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
            return
        if run is None:
            widget.set_notice("ответ идёт…")
            return
        widget.set_notice(f"Загрузка модели: {run.loading}" if run.loading else "")
        widget.set_reasoning(run.reasoning, streaming=not run.text)
        widget.set_text(run.text)

    # --- misc ---

    def show_error(self, message: str) -> None:
        log.warning("%s", message)
        self.statusBar().showMessage(message, 10000)

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
