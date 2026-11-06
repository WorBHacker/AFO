"""A long chat: 1000 messages (code, lists, tables, tool calls) read from the hub and opened in the window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from starlette.testclient import TestClient
from test_ui import FakeClient, QApplication, wait_until

from app import render
from app.ui import rows
from common.paths import data_paths
from hub import store
from hub.server import create_app
from hub.settings import HubSettings

from .stand import Clock, note

PAIRS = 500
CODE = "\n".join(f"    total += item.price * {n}  # строка {n}" for n in range(15))


def answer(n: int) -> str:
    text = (f"### Ответ {n}\n\nВот что получилось: функция считает **итог** и `проверяет` данные.\n\n"
            f"- первый шаг\n- второй шаг\n- третий шаг\n\n```python\ndef total_{n}(items):\n    total = 0\n"
            f"    for item in items:\n{CODE}\n    return total\n```\n\nГотово.")  # fmt: skip
    if n % 10 == 0:
        text += "\n\n| файл | строк |\n|---|---|\n| a.py | 10 |\n| b.py | 20 |"
    return text


def fill(conn, cid: str) -> None:
    for n in range(PAIRS):
        store.add_message(conn, cid, "user", [{"type": "text", "text": f"Вопрос {n}: как посчитать итог?"}])
        content = [{"type": "reasoning", "text": "подумать " * 30}] if n % 3 == 0 else []
        content.append({"type": "text", "text": answer(n)})
        if n % 5 == 0:
            content.append({"type": "tool_call", "id": f"t{n}", "name": "read_file",
                            "input": {"project": "p", "path": f"src/m{n}.py"}})  # fmt: skip
        store.add_message(conn, cid, "assistant", content, model_id="anthropic/claude-opus-5-5")
        if n % 5 == 0:
            output = "\n".join(f"{i}| print({i})" for i in range(80))
            store.add_message(conn, cid, "tool", [{"type": "tool_result", "id": f"t{n}", "name": "read_file",
                                                   "output": output}])  # fmt: skip


def test_long_reasoning_opened_as_one_text(qapp):
    """24.2: a finished reasoning up to Thinking.qml's `wholeSize` (100 000 characters) is one text - a selection
    goes through it; opening it lays it all out at once."""
    import time

    from app.ui.main import QuickApp

    reasoning = ("Сначала проверю, что функция получает список, а не генератор; потом тесты рядом. " * 3 + "\n") * 400
    reasoning = reasoning[:100_000]
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        fake = FakeClient()
        fake.histories["c1"] = {"id": "c1", "title": "Размышления", "busy": False, "messages": [
            {"id": 1, "role": "user", "model_id": None, "usage": None, "content": [{"type": "text", "text": "?"}]},
            {"id": 2, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
             "content": [{"type": "reasoning", "text": reasoning}, {"type": "text", "text": "Ответ."}]},
        ]}  # fmt: skip
        app.attach(fake, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openChat("c1")
        wait_until(lambda: len(app.messages.rows) == 2)
        for _ in range(10):
            QApplication.processEvents()
        swapped = []
        quick.window.frameSwapped.connect(lambda: swapped.append(time.perf_counter()))
        began = time.perf_counter()
        app.toggleThinking(1)
        wait_until(lambda: swapped, timeout=10)

        def one() -> bool:
            todo = [quick.window.contentItem()]
            while todo:
                item = todo.pop()
                todo.extend(item.childItems())
                if item.objectName() == "thinkingParts":
                    found = [c for c in item.childItems() if c.property("part") is not None]
                    if len(found) == 1 and found[0].property("text") == reasoning:
                        return True
            return False

        wait_until(one)
        note("окно: открыть размышления в 100 000 знаков одним текстом", (swapped[0] - began) * 1000, "мс", 100)
        assert quick.warnings == []
    finally:
        quick.close()


def test_the_window_rests_while_an_answer_waits(qapp):
    """24.5: frames and CPU over 3 s with nothing new on screen - an answer waiting (the pulsing dot), then after it -
    in a 1000-message chat at 2560×1400."""
    import time

    from test_ui import event

    from app.ui.main import QuickApp

    messages = []
    for n in range(PAIRS):
        messages.append({"id": len(messages) + 1, "role": "user", "model_id": None, "usage": None,
                         "content": [{"type": "text", "text": f"Вопрос {n}"}]})  # fmt: skip
        messages.append({"id": len(messages) + 1, "role": "assistant", "model_id": "llama-swap/coder", "usage": None,
                         "content": [{"type": "text", "text": answer(n)}]})  # fmt: skip
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        quick.window.resize(2560, 1400)
        app = quick.controller
        fake = FakeClient()
        fake.histories["c1"] = {"id": "c1", "title": "Длинный", "busy": False, "messages": messages}
        app.attach(fake, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openChat("c1")
        wait_until(lambda: len(app.messages.rows) >= PAIRS, timeout=60)
        swapped = []
        quick.window.frameSwapped.connect(lambda: swapped.append(1))

        def quiet() -> tuple[float, float]:
            for _ in range(50):
                QApplication.processEvents()
                time.sleep(0.01)
            swapped.clear()
            with Clock() as clock:
                began = time.perf_counter()
                while time.perf_counter() - began < 3:
                    QApplication.processEvents()
                    time.sleep(0.002)
            return len(swapped) / clock.s, clock.cpu / clock.s * 100

        app.on_event(event("chat.run_started", model_id="llama-swap/coder"))
        app.on_event(event("chat.delta", kind="content", text="Ответ идёт"))
        app._render_live()
        waiting, waiting_cpu = quiet()
        app.on_event(event("chat.done", message_id=PAIRS * 2 + 1))
        app._render_live()
        after, after_cpu = quiet()
        note("окно: ответ ждёт, нового нет — кадров в секунду", waiting, "шт", 5)
        note("  CPU окна, % ядра", waiting_cpu, "%", 25)
        note("окно: после ответа — кадров в секунду", after, "шт", 2)
        note("  CPU окна, % ядра", after_cpu, "%", 25)
        assert quick.warnings == []
    finally:
        quick.close()


def test_a_long_chat_from_the_hub_and_in_the_window(qapp):
    from app.ui.main import QuickApp

    hub = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(hub, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        cid = client.post("/v1/conversations", json={"title": "Длинный"}).json()["id"]
        hub.state.db.call(lambda conn: fill(conn, cid))
        client.get(f"/v1/conversations/{cid}")  # warm
        with Clock() as read:
            got = client.get(f"/v1/conversations/{cid}")
        history = got.json()
    assert len(history["messages"]) == PAIRS * 2 + PAIRS // 5
    note("хаб: чат из 1000 сообщений, GET", read.s * 1000, "мс", 200)
    note("хаб: его ответ", len(got.content) / 1024, "КБ", 2000)

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        fake = FakeClient()
        fake.histories["c1"] = {**history, "id": "c1"}
        app.attach(fake, start_events=False)
        wait_until(lambda: not app.bg.busy)
        with Clock() as built:
            rows.rows_for(history["messages"], app.models)
        note("окно: строки чата, первый раз после старта", built.s * 1000, "мс", 3000)
        render._highlighted.cache_clear()  # another chat's code: nothing of it coloured yet

        shown = Clock()
        real = app._show_conversation

        def timed(*args):
            with shown:
                real(*args)

        app._show_conversation = timed
        with Clock() as opened:
            app.openChat("c1")
            wait_until(lambda: len(app.messages.rows) >= PAIRS, timeout=60)
        note("окно: открыть чат, всего", opened.s * 1000, "мс", 4000)
        note("окно: из них поток окна занят", shown.s * 1000, "мс", 300)

        # a theme switch renders again the rows in sight, the others as the scroll comes to them (24.3)
        for _ in range(30):
            QApplication.processEvents()
        made = []
        real_restyled = rows.restyled
        rows.restyled = lambda row: made.append(1) or real_restyled(row)
        try:
            with Clock() as themed:
                app.setTheme("dark" if not app.theme.dark else "light")
        finally:
            rows.restyled = real_restyled
        note("окно: смена темы в этом чате", themed.s * 1000, "мс", 300)
        note("  строк отрисовано заново сразу", len(made), "шт", 60)
        assert made and quick.warnings == []
    finally:
        quick.close()
