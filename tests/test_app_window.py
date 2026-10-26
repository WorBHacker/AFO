"""The window, offscreen, against a fake hub client: what it shows for history, a streamed answer and errors."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import theme
from app.window import MainWindow
from common.paths import data_paths

MODELS = [
    {
        "id": "anthropic/claude-opus-5-5",
        "name": "Claude Opus 5.5",
        "locality": "cloud",
        "prices": {"input": 4.0, "output": 20.0},
    },
    {"id": "llama-swap/coder", "name": "Coder", "locality": "local", "prices": {}},
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
            "content": [{"type": "reasoning", "text": "подумать"}, {"type": "text", "text": "**Привет!**"}],
            "usage": {"output_tokens": 12, "cost_usd": 0.0003, "stats": {"tok_s": 30.0}},
        },
    ],
}


class FakeClient:
    token = "t"
    events_url = "ws://127.0.0.1:1/v1/events"

    def __init__(self):
        self.sent = []
        self.cancelled = []

    def models(self):
        return MODELS

    def conversations(self):
        return {"conversations": [{"id": "c1", "title": "Первый"}], "busy": []}

    def conversation(self, cid):
        return HISTORY

    def status(self):
        return STATUS

    def usage(self):
        return {"today_cost_usd": 0.0003, "total_cost_usd": 0.5}

    def create_conversation(self):
        return "c2"

    def send(self, cid, text, model_id, effort):
        self.sent.append((cid, text, model_id, effort))
        return {}

    def cancel(self, cid):
        self.cancelled.append(cid)
        return True

    def close(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met"
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


@pytest.fixture
def window(qapp, tmp_path):
    w = MainWindow(data_paths().ensure(), settings=QSettings(str(tmp_path / "app.ini"), QSettings.Format.IniFormat))
    w.attach(FakeClient(), start_events=False)
    wait_until(lambda: not w.bg.busy)
    yield w
    w.close()


def texts(window):
    return [(m.role, m.header.text(), m.body.text()) for m in window.transcript.messages()]


def test_models_status_usage_and_chats(window):
    labels = [window.model_box.itemText(i) for i in range(window.model_box.count())]
    assert labels[0] == "Coder · локальная"  # local first
    assert "Claude Opus 5.5 · $4/$20 за 1M" in labels
    assert "VRAM 8.0/16.0 ГБ" in window.status_label.text()
    assert window.usage_label.text() == "Расходы: сегодня $0.0003 · всего $0.50"
    assert window.chat_list.item(0).text() == "Первый"


def test_opening_a_chat_shows_history_and_picks_its_model(window):
    window.chat_list.setCurrentRow(0)
    wait_until(lambda: len(window.transcript.messages()) == 2)
    user, answer = window.transcript.messages()
    assert user.role == "user" and "привет" in user.body.text()
    assert answer.header.text() == "Claude Opus 5.5"
    assert "<strong>Привет!</strong>" in answer.body.text()
    assert answer.think_toggle.isVisibleTo(answer) and "Размышления (8" in answer.think_toggle.text()
    assert not answer.think.isVisibleTo(answer)  # collapsed until asked
    answer.think_toggle.setChecked(True)
    assert answer.think.isVisibleTo(answer) and answer.think.text() == "подумать"
    assert answer.footer.text() == "30.0 т/с · 12 ток. · $0.0003"
    assert window.model_box.currentData() == "anthropic/claude-opus-5-5"


def test_a_streamed_answer_then_done(window):
    window.chat_list.setCurrentRow(0)
    wait_until(lambda: len(window.transcript.messages()) == 2)
    on = window.on_event
    on({"type": "chat.run_started", "payload": {"conversation_id": "c1", "model_id": "llama-swap/coder"}})
    assert window.send_button.text() == "Стоп"
    on({"type": "chat.loading", "payload": {"conversation_id": "c1", "text": "llama-swap loading model: coder\n"}})
    wait_until(lambda: window.live_widget.notice.text().startswith("Загрузка модели"))
    on({"type": "chat.delta", "payload": {"conversation_id": "c1", "kind": "reasoning", "text": "хм"}})
    on({"type": "chat.delta", "payload": {"conversation_id": "c1", "kind": "content", "text": "Ответ "}})
    on({"type": "chat.delta", "payload": {"conversation_id": "c1", "kind": "content", "text": "`код`"}})
    live = window.live_widget
    wait_until(lambda: "<code>код</code>" in live.body.text())
    assert live.header.text() == "Coder · отвечает…"
    assert not live.notice.isVisibleTo(live)

    final = {
        "id": 3,
        "role": "assistant",
        "model_id": "llama-swap/coder",
        "content": [{"type": "text", "text": "Ответ"}],
    }
    on({"type": "chat.message", "payload": {"conversation_id": "c1", "message": final}})
    on(
        {
            "type": "chat.done",
            "payload": {
                "conversation_id": "c1",
                "message_id": 3,
                "usage": {"output_tokens": 2},
                "stats": {"tok_s": 99.0},
                "cost_usd": 0.0,
                "cancelled": False,
                "stop_reason": "stop",
            },
        }
    )
    assert window.live_widget is None
    assert len(window.transcript.messages()) == 3  # the live widget became the saved answer
    assert live.header.text() == "Coder"
    assert live.footer.text() == "99.0 т/с · 2 ток."
    assert window.send_button.text() == "Отправить"


def test_deltas_of_another_chat_wait_in_the_background(window):
    window.chat_list.setCurrentRow(0)
    wait_until(lambda: len(window.transcript.messages()) == 2)
    window.on_event({"type": "chat.run_started", "payload": {"conversation_id": "other", "model_id": "x"}})
    window.on_event({"type": "chat.delta", "payload": {"conversation_id": "other", "kind": "content", "text": "фон"}})
    assert window.live_widget is None
    assert window.live["other"].text == "фон"


def test_refusal_is_shown_with_its_cost(window):
    window.chat_list.setCurrentRow(0)
    wait_until(lambda: len(window.transcript.messages()) == 2)
    window.on_event({"type": "chat.run_started", "payload": {"conversation_id": "c1", "model_id": "x"}})
    payload = {"conversation_id": "c1", "detail": "Claude declined this request.", "refusal": True, "cost_usd": 0.002}
    window.on_event({"type": "chat.error", "payload": payload})
    error = window.transcript.messages()[-1]
    assert error.role == "error"
    assert "declined" in error.body.text() and "$0.0020" in error.body.text()
    assert window.live_widget is None
    assert window.send_button.text() == "Отправить"


def test_send_creates_a_chat_and_stop_cancels(window):
    window.new_chat()
    window.model_box.setCurrentIndex(window.model_box.findData("llama-swap/coder"))
    window.composer.setPlainText("  вопрос  ")
    window.on_send_clicked()
    wait_until(lambda: window.current == "c2")
    assert window.client.sent == [("c2", "вопрос", "llama-swap/coder", "medium")]
    assert window.composer.toPlainText() == ""
    assert window.send_button.text() == "Стоп"
    window.on_send_clicked()
    wait_until(lambda: window.client.cancelled == ["c2"])


def test_copy_link_puts_the_code_on_the_clipboard(window):
    window.chat_list.setCurrentRow(0)
    wait_until(lambda: len(window.transcript.messages()) == 2)
    answer = window.transcript.messages()[1]
    answer.set_text("```python\nprint('x')\n```")
    answer.body.linkActivated.emit("copy:0")
    assert QApplication.clipboard().text() == "print('x')"


def test_titles_and_status_events(window):
    window.on_event({"type": "chat.titled", "payload": {"conversation_id": "c1", "title": "Новое имя"}})
    assert window.chat_list.item(0).text() == "Новое имя"
    tight = {**STATUS, "memory": {**STATUS["memory"], "commit_used_gib": 77.0}}
    window.on_event({"type": "system.status", "payload": tight})
    assert window.status_label.property("warn") is True
    assert "файл подкачки" in window.status_label.toolTip()
