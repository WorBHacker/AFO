"""Scrolling (2026-11-05, «прокрутка иногда лагает»): the 1000-message chat and the settings sheet at 2560×1400 - for
each wheel notch, the time from moving the view to the frame that shows it (the software scene graph, no screen).
The second pass over the chat uses its rows again (ChatView.reuseItems)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import statistics
import time

from starlette.testclient import TestClient
from test_ui import FakeClient, wait_until

from common.paths import data_paths
from hub.server import create_app
from hub.settings import HubSettings

from .stand import note
from .test_chat import PAIRS, fill

STEP, STEPS = 100, 150  # px a notch, notches


def frames(qapp, window, flick) -> list[float]:
    swapped: list[float] = []
    window.frameSwapped.connect(lambda: swapped.append(time.perf_counter()))
    top = 0.0
    flick.setProperty("contentY", top)
    for _ in range(20):
        qapp.processEvents()
    took = []
    for _ in range(STEPS):
        room = flick.property("contentHeight") - flick.property("height")
        top = top + STEP if top + STEP < room else 0.0
        before, began = len(swapped), time.perf_counter()
        flick.setProperty("contentY", top)
        while len(swapped) == before and time.perf_counter() - began < 2:
            qapp.processEvents()
        took.append((swapped[-1] - began) * 1000 if len(swapped) > before else 2000.0)
    return sorted(took)


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def test_scrolling_the_chat_and_the_settings(qapp):
    from PySide6.QtCore import QObject

    from app.ui.main import QuickApp

    hub = create_app(HubSettings(paths=data_paths(), llama_swap_url="http://127.0.0.1:9", status_interval_s=0,
                                 process_interval_s=0))  # fmt: skip
    with TestClient(hub, base_url="http://127.0.0.1:8765") as client:
        client.headers["Authorization"] = "Bearer " + (data_paths().tokens / "app.token").read_text(encoding="utf-8")
        cid = client.post("/v1/conversations", json={"title": "Длинный"}).json()["id"]
        hub.state.db.call(lambda conn: fill(conn, cid))
        history = client.get(f"/v1/conversations/{cid}").json()
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        window = quick.window
        window.resize(2560, 1400)
        app = quick.controller
        fake = FakeClient()
        fake.histories["c1"] = {**history, "id": "c1"}
        app.attach(fake, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.openChat("c1")
        wait_until(lambda: len(app.messages.rows) >= PAIRS, timeout=60)
        chat = next(i for i in walk(window.contentItem()) if i.metaObject().className().startswith("ChatView"))
        first = frames(qapp, window, chat)
        again = frames(qapp, window, chat)
        note("прокрутка чата (1000 сообщений, 2560×1400): кадр, медиана", statistics.median(first), "мс", 40)
        note("прокрутка чата, второй проход: 90 % кадров не дольше", again[int(len(again) * 0.9)], "мс", 40)
        note("прокрутка чата, второй проход: самый долгий кадр", again[-1], "мс", 80)
        settings = window.findChild(QObject, "settingsSheet")
        settings.open()
        for _ in range(100):
            qapp.processEvents()
            time.sleep(0.01)
        flick = next(i for i in walk(window.contentItem().parentItem() or window.contentItem())
                     if i.objectName() == "sheetFlick" and i.isVisible() and i.property("height") > 100)  # fmt: skip
        sheet = frames(qapp, window, flick)
        note("прокрутка настроек: 90 % кадров не дольше", sheet[int(len(sheet) * 0.9)], "мс", 30)
        assert quick.warnings == []
    finally:
        quick.close()
