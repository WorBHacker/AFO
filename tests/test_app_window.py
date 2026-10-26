"""The window, offscreen, against a fake hub client: history, a streamed answer with its thinking timer,
code blocks, errors, and the status bar."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QAbstractItemView, QApplication

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
            "usage": {"output_tokens": 12, "cost_usd": 0.0003, "stats": {"tok_s": 30.0, "think_s": 2.5}},
        },
    ],
}


class FakeClient:
    token = "t"
    base_url = "http://127.0.0.1:8765"
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


def open_first_chat(window):
    window.chat_list.setCurrentRow(0)
    wait_until(lambda: len(window.transcript.messages()) == 2)
    return window.transcript.messages()


def event(event_type, **payload):
    return {"type": event_type, "payload": {"conversation_id": "c1", **payload}}


def test_models_chats_and_pixel_scrolling(window):
    labels = [window.model_box.itemText(i) for i in range(window.model_box.count())]
    assert labels[0] == "Coder · локальная"  # local first
    assert "Claude Opus 5.5 · $4/$20 за 1M" in labels
    assert window.chat_list.item(0).text() == "Первый"
    per_pixel = QAbstractItemView.ScrollMode.ScrollPerPixel
    assert window.chat_list.verticalScrollMode() == per_pixel
    assert window.model_box.view().verticalScrollMode() == per_pixel


def test_status_bar(window):
    assert window.statusBar().maximumHeight() == 26
    assert "Хаб :8765" in window.hub_label.text()
    assert window.model_label.text() == "Модель: Coder"
    assert window.session_label.text() == "Сессия: 0 ток. · $0"
    assert "сегодня $0.0003 · всего $0.50" in window.session_label.toolTip()
    assert window.local_label.text() == "coder · готова"
    assert window.vram_text.text() == "8.0/16.0 ГБ" and window.vram_bar.value() == 500
    assert window.commit_text.text() == "40/79 ГБ"
    assert window.gpu_label.text() == "GPU 5% · 40°C"
    tight = {**STATUS, "memory": {**STATUS["memory"], "commit_used_gib": 77.0}}
    window.on_event({"type": "system.status", "payload": tight})
    assert window.commit_bar.property("warn") is True
    assert "файл подкачки" in window.commit_text.toolTip()


def test_history_with_a_folded_thinking_block(window):
    user, answer = open_first_chat(window)
    assert user.role == "user" and "привет" in user.prose_html()
    assert answer.header.text() == "Claude Opus 5.5"
    assert "<strong>Привет!</strong>" in answer.prose_html()
    thinking = answer.thinking
    assert thinking.isVisibleTo(answer)
    assert thinking.header.text() == "▸  Размышления · 2.5 с"
    assert not thinking.body.isVisibleTo(answer)  # folded until asked
    thinking.header.setChecked(True)
    assert thinking.body.isVisibleTo(answer) and "подумать" in thinking.body.text()
    assert thinking.header.text().startswith("▾")
    assert answer.footer.text() == "30.0 т/с · 12 ток. · $0.0003"
    assert window.model_box.currentData() == "anthropic/claude-opus-5-5"  # the chat's model is picked
    assert window.model_label.text() == "Модель: Claude Opus 5.5"


def test_a_streamed_answer_with_its_thinking_timer(window):
    open_first_chat(window)
    on = window.on_event
    on(event("chat.run_started", model_id="llama-swap/coder"))
    assert window.send_button.text() == "Стоп"
    on(event("chat.loading", text="llama-swap loading model: coder\n"))
    wait_until(lambda: window.live_widget.notice.text().startswith("Загрузка модели"))
    on(event("chat.delta", kind="reasoning", text="хм"))
    live = window.live_widget
    wait_until(lambda: "Думает…" in live.thinking.header.text())
    first = live.thinking.header.text()
    wait_until(lambda: live.thinking.header.text() != first)  # the timer ticks while it thinks
    on(event("chat.delta", kind="content", text="Ответ "))
    on(event("chat.delta", kind="content", text="`код`"))
    wait_until(lambda: "<code>код</code>" in live.prose_html())
    assert "Размышления ·" in live.thinking.header.text()  # stopped once the answer began
    assert live.header.text() == "Coder · отвечает…"
    assert not live.notice.isVisibleTo(live)

    final = {
        "id": 3,
        "role": "assistant",
        "model_id": "llama-swap/coder",
        "content": [{"type": "text", "text": "Ответ"}],
    }
    on(event("chat.message", message=final))
    stats = {"tok_s": 99.0, "think_s": 1.5}
    usage = {"input_tokens": 40, "output_tokens": 2}
    on(event("chat.done", message_id=3, usage=usage, stats=stats, cost_usd=0.0, cancelled=False))
    assert window.live_widget is None
    assert len(window.transcript.messages()) == 3  # the live card became the saved answer
    assert live.header.text() == "Coder"
    assert live.footer.text() == "99.0 т/с · 2 ток."
    assert window.send_button.text() == "Отправить"
    assert window.session_label.text() == "Сессия: 42 ток. · $0"


def test_code_blocks_have_a_header_and_a_copy_button(window):
    _user, answer = open_first_chat(window)
    answer.set_markdown("Вот:\n\n```python\nprint('x')\n```\n\nи всё")
    (block,) = answer.code_blocks()
    assert block.lang.text() == "python"
    assert not block.diff_button.isVisibleTo(block)  # reserved for the diff view
    block.copy_button.click()
    assert QApplication.clipboard().text() == "print('x')"
    assert block.copy_button.text() == "Скопировано ✓"
    assert window.flash_label.text() == "Код скопирован"
    answer.set_markdown("Вот:\n\n```python\nprint('x')\n```\n\nи всё, и ещё")
    assert answer.code_blocks() == [block]  # streaming more text keeps the same block widget


def test_deltas_of_another_chat_wait_in_the_background(window):
    open_first_chat(window)
    window.on_event({"type": "chat.run_started", "payload": {"conversation_id": "other", "model_id": "x"}})
    window.on_event({"type": "chat.delta", "payload": {"conversation_id": "other", "kind": "content", "text": "фон"}})
    assert window.live_widget is None
    assert window.live["other"].text == "фон"


def test_refusal_is_shown_with_its_cost(window):
    open_first_chat(window)
    window.on_event(event("chat.run_started", model_id="x"))
    window.on_event(
        event(
            "chat.error",
            detail="Claude declined this request.",
            refusal=True,
            cost_usd=0.002,
            usage={"input_tokens": 100, "output_tokens": 5},
        )
    )
    error = window.transcript.messages()[-1]
    assert error.role == "error"
    assert "declined" in error.prose_html() and "$0.0020" in error.prose_html()
    assert window.live_widget is None
    assert window.send_button.text() == "Отправить"
    assert window.session_label.text() == "Сессия: 105 ток. · $0.0020"


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


def test_the_composer_grows_with_its_text(window):
    window.show()
    small = window.composer.height()
    window.composer.setPlainText("\n".join(f"строка {i}" for i in range(6)))
    QApplication.processEvents()
    assert window.composer.height() > small
    window.composer.setPlainText("\n".join(f"строка {i}" for i in range(60)))
    QApplication.processEvents()
    assert window.composer.height() == window.composer.MAX_HEIGHT


def test_titles_follow_events(window):
    window.on_event({"type": "chat.titled", "payload": {"conversation_id": "c1", "title": "Новое имя"}})
    assert window.chat_list.item(0).text() == "Новое имя"


def test_light_and_dark_switch_on_the_fly(window):
    from app import palette

    _user, answer = open_first_chat(window)
    answer.set_markdown("текст\n\n```python\nx = 1\n```")
    window.set_theme("dark")
    assert palette.current() is palette.DARK
    dark_prose, dark_code = answer.prose_html(), answer.code_blocks()[0].view.toHtml()
    assert palette.DARK.text_soft in dark_prose
    assert palette.DARK.window in QApplication.instance().styleSheet()

    window.set_theme("light")
    assert palette.current() is palette.LIGHT
    assert window.theme_actions["light"].isChecked()
    assert palette.LIGHT.text_soft in answer.prose_html()  # the open chat re-rendered its HTML
    assert answer.code_blocks()[0].view.toHtml() != dark_code  # and its code colours
    assert palette.LIGHT.window in QApplication.instance().styleSheet()
    assert f"color:{palette.LIGHT.ok}" in window.hub_label.text().replace(" ", "")
    assert window.settings.value("ui/theme") == "light"


def test_system_mode_follows_windows(window):
    from PySide6.QtCore import Qt

    from app import palette

    window.set_theme("system")
    scheme = QApplication.instance().styleHints().colorScheme()
    expected = palette.LIGHT if scheme == Qt.ColorScheme.Light else palette.DARK
    assert palette.current() is expected
