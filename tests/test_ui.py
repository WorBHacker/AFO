"""The Qt Quick window: its controller against a fake hub client, its row builders, and the real QML loaded with
history and a streamed answer (no QML warnings allowed). Offscreen, software scene graph."""

import os
import time
from datetime import UTC, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtWidgets import QApplication

from app import palette
from app.hub_client import HubError
from app.ui import rows
from app.ui.controller import Controller
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


class FakeClient:
    token = "t"
    base_url = "http://127.0.0.1:8765"
    events_url = "ws://127.0.0.1:1/v1/events"

    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.deleted = []
        self.over_budget = None

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
        return HISTORY

    def status(self):
        return STATUS

    def usage(self):
        return {"today_cost_usd": 0.0003, "total_cost_usd": 0.5}

    def budgets(self):
        return []

    def create_conversation(self):
        return "c2"

    def send(self, cid, text, model_id, effort, confirm_over_budget=False):
        if self.over_budget is not None and not (confirm_over_budget and self.over_budget):
            data = {"code": "budget_exceeded", "budgets": [REACHED], "can_confirm": self.over_budget}
            raise HubError("budget reached", code="budget_exceeded", data=data)
        self.sent.append((cid, text, model_id, effort, confirm_over_budget))
        return {}

    def cancel(self, cid):
        self.cancelled.append(cid)
        return True

    def delete_conversation(self, cid):
        self.deleted.append(cid)

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
