"""The window, offscreen, against a fake hub client: history, a streamed answer with its thinking timer,
code blocks, errors, and the status bar."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QAbstractItemView, QApplication

from app import theme
from app.hub_client import HubError
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
REACHED = {**BUDGET, "spent_usd": 10.4, "state": "hard"}
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
        self.deleted = []
        self.saved_budgets = []
        self.over_budget = None  # None, or whether the reached budget lets the user confirm
        self.budget_list = [BUDGET]

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

    def send(self, cid, text, model_id, effort, confirm_over_budget=False):
        if self.over_budget is not None and not (confirm_over_budget and self.over_budget):
            data = {"code": "budget_exceeded", "budgets": [REACHED], "can_confirm": self.over_budget}
            raise HubError("budget reached", code="budget_exceeded", data=data)
        self.sent.append((cid, text, model_id, effort, confirm_over_budget))
        return {}

    def delete_conversation(self, cid):
        self.deleted.append(cid)

    def budgets(self):
        return self.budget_list

    def set_budget(self, **body):
        self.saved_budgets.append(body)
        return {**BUDGET, **body}

    def delete_budget(self, budget_id):
        self.deleted.append(budget_id)

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
    assert window.client.sent == [("c2", "вопрос", "llama-swap/coder", "medium", False)]
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


def send_new(window, text="дорогой вопрос"):
    window.new_chat()
    window.model_box.setCurrentIndex(window.model_box.findData("anthropic/claude-opus-5-5"))
    window.composer.setPlainText(text)
    window.on_send_clicked()


def test_a_reached_budget_asks_and_sends_confirmed(window, monkeypatch):
    questions = []
    monkeypatch.setattr(window, "_confirm", lambda q: questions.append(q) or True)
    window.client.over_budget = True
    send_new(window)
    wait_until(lambda: window.current == "c2")
    assert "Все платные модели за месяц: $10.40 из $10.00" in questions[0]
    assert window.client.sent == [("c2", "дорогой вопрос", "anthropic/claude-opus-5-5", "medium", True)]
    assert window.client.deleted == []


def test_declining_keeps_the_text_and_removes_the_empty_chat(window, monkeypatch):
    monkeypatch.setattr(window, "_confirm", lambda q: False)
    window.client.over_budget = True
    send_new(window)
    wait_until(lambda: window.client.deleted == ["c2"])
    assert window.client.sent == []
    assert window.composer.toPlainText() == "дорогой вопрос"
    assert window.current is None


def test_a_blocking_budget_is_not_offered_for_confirmation(window, monkeypatch):
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda q: asked.append(q) or True)
    window.client.over_budget = False
    send_new(window)
    wait_until(lambda: "запрещены" in window.flash_label.text())
    assert asked == [] and window.client.sent == []


def test_budget_events_flash_and_mark_the_session(window):
    assert "Бюджеты:" in window.session_label.toolTip()
    assert "Все платные модели за месяц: $1.25 из $10.00" in window.session_label.toolTip()
    assert window.session_label.property("warn") is False
    window.client.budget_list = [{**BUDGET, "spent_usd": 6.0, "state": "soft"}]
    window.on_event({"type": "budget.warning", "payload": {"budget": {**BUDGET, "spent_usd": 6.0}}})
    assert "порог предупреждения $5.00" in window.flash_label.text()
    wait_until(lambda: window.session_label.property("warn") is True)


def test_budget_dialog_lists_saves_and_deletes(window):
    from app.budget_dialog import BudgetDialog

    dialog = BudgetDialog(window)
    wait_until(lambda: dialog.table.rowCount() == 1)
    assert dialog.table.item(0, 0).text() == "Все платные модели за месяц"
    assert dialog.table.item(0, 1).text() == "$1.25"
    dialog.table.selectRow(0)
    assert dialog.hard_box.value() == 10.0  # the selected budget fills the form
    dialog.scope_box.setCurrentIndex(dialog.scope_box.findData("provider:anthropic"))
    dialog.period_box.setCurrentIndex(dialog.period_box.findData("day"))
    dialog.soft_box.setValue(0)  # "нет"
    dialog.hard_box.setValue(3)
    dialog.action_box.setCurrentIndex(dialog.action_box.findData("block"))
    dialog.save()
    wait_until(lambda: window.client.saved_budgets)
    assert window.client.saved_budgets[0] == {
        "scope": "provider",
        "provider": "anthropic",
        "period": "day",
        "soft_usd": None,
        "hard_usd": 3.0,
        "hard_action": "block",
    }
    dialog.table.selectRow(0)
    dialog.delete_selected()
    wait_until(lambda: 1 in window.client.deleted)
    dialog.close()
