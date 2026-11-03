"""A long chat: 1000 messages (code, lists, tables, tool calls) read from the hub and opened in the window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from starlette.testclient import TestClient
from test_ui import FakeClient, wait_until

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

        with Clock() as themed:
            app.setTheme("light")
        note("окно: смена темы в этом чате", themed.s * 1000, "мс", 4000)
        assert quick.warnings == []
    finally:
        quick.close()
