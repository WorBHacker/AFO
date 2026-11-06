"""A chatty terminal: 200 000 lines through the hub (ConPTY, the log, `terminal.output` events) and into the
window's emulator, which plays every event of the terminal on screen."""

import asyncio
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_ui import FakeClient, wait_until

from common.paths import data_paths
from hub.db import Database, migrate
from hub.terminals import TerminalManager

from .stand import Clock, note

LINES = 200_000


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, event_type, payload=None, *, project_id=None, persist=True):
        self.events.append((event_type, payload))


def test_wide_characters_in_the_windows_emulator():
    """24.4: characters two cells wide (CJK) drawn as quickly as narrow ones - pyte drew them 2-3 times slower."""
    import pyte

    from app.ui.term_screen import TermScreen

    class Pyte(TermScreen):
        draw = pyte.Screen.draw

    def played(screen_class, line: str) -> float:
        screen = screen_class(120, 30)
        stream = pyte.Stream(screen)
        text = "".join(f"\x1b[32m{n:>6}\x1b[0m {line}\r\n" for n in range(20_000))
        with Clock() as clock:
            stream.feed(text)
        return clock.s * 1000

    cjk = "日本語のログ行：処理が完了しました。結果を保存します。"
    latin = "a log line: the work is done, the result is saved now."
    note("терминал в окне: 20 000 строк иероглифами", played(TermScreen, cjk), "мс", 3000)
    note("  они же у pyte", played(Pyte, cjk), "мс", 20_000)
    note("  20 000 строк латиницей", played(TermScreen, latin), "мс", 3000)


def test_200_000_lines_through_the_hub_and_the_window(tmp_path, qapp):
    from app.ui.main import QuickApp

    db = Database(data_paths().ensure().db)
    db.call(migrate)
    script = tmp_path / "chatty.py"
    script.write_text(f"for i in range({LINES}):\n    print('строка', i, 'из', {LINES})\n", encoding="utf-8")

    async def scenario():
        bus = Bus()
        terminals = TerminalManager(bus, db, data_paths().terminals)
        await terminals.start(prune=False)
        command = f"& '{sys.executable}' {script.name}"  # PowerShell: the path has a space in it
        await terminals.create(cwd=tmp_path, by="app", command=command, shell="powershell")
        started = time.perf_counter()
        while not any(t == "terminal.exited" for t, _ in bus.events):
            assert time.perf_counter() - started < 300, "the command did not end"
            await asyncio.sleep(0.05)
        await terminals.stop()
        return [p for t, p in bus.events if t == "terminal.output"]

    with Clock() as hub:
        outputs = asyncio.run(scenario())
    db.close()
    text = "".join(p["data"] for p in outputs)
    assert f"строка {LINES - 1} из {LINES}" in text
    note("терминал: 200 000 строк через хаб", hub.s, "с", 20)
    note("  событий terminal.output", len(outputs), "шт", 2000)
    note("  CPU хаба на них", hub.cpu, "с", 10)

    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app, terms = quick.controller, quick.controller.terminals
        fake = FakeClient()
        fake.term_text = ""  # the window's t1 is the hub's terminal: it starts empty
        app.attach(fake, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("projects")
        app.projects.openProject("p1")
        wait_until(lambda: terms.tabs)
        terms.setViewSize(120, 30)
        terms.setPanelOpen(True)
        terms.openTerminal("t1")
        emu = terms._emus["t1"]
        wait_until(lambda: emu.loaded and not emu.loading)
        with Clock() as window:
            for payload in outputs:
                app.on_event({"type": "terminal.output", "payload": {**payload, "terminal_id": "t1"}})
        assert f"строка {LINES - 1} из {LINES}" in emu.screen.plain_text() and not emu.missed
        note("окно: те же события в терминал на экране", window.s, "с", 10)
        note("  на 1000 строк", window.s * 1000 / (LINES / 1000), "мс", 40)  # how ConPTY cuts them varies
        note("  в среднем на событие", window.s * 1000 / len(outputs), "мс", 500)
    finally:
        quick.close()
