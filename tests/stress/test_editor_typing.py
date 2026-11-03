"""The editor on a 1 MB Python file (D47, step 14.2b; the outside review's thresholds): opening it, its colours -
the screen's reach at once, then from the whole text's lexing in short slices, the middle of the file when the
screen gets there - and a key's handling. A key's number is the window's handling of it (the TextEdit, the editor's
keys, the line coloured at once), not the frame drawn after."""

import os
import statistics
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pygments.token import Keyword
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtQuick import QQuickItem
from test_ui import FakeClient, QApplication, wait_until

from app import palette
from app.ui import highlighter
from common.paths import data_paths

from .stand import Clock, note


def big_file() -> str:
    """Just under the editor's limit (1 MB in bytes: the Cyrillic of the docstrings is two each)."""
    parts, size, n = [], 0, 0
    while True:
        part = (
            f'def total_{n}(items):\n    """Итог по позициям {n}: цена на количество."""\n    total = 0\n'
            f"    for item in items:\n        total += item.price * {n}  # строка {n}\n    return total\n\n"
        )
        if size + len(part.encode()) > 1024 * 1024:
            return "".join(parts)
        parts.append(part)
        size += len(part.encode())
        n += 1


def test_a_megabyte_in_the_editor(qapp):
    from app.ui.main import QuickApp

    text = big_file()
    quick = QuickApp(qapp, data_paths().ensure(), connect=False)
    try:
        app = quick.controller
        client = FakeClient()
        client.files["big.py"] = text
        app.attach(client, start_events=False)
        wait_until(lambda: not app.bg.busy)
        app.setMode("projects")
        app.projects.openProject("p1")
        app.projects.openFile("big.py", False)
        wait_until(lambda: app.projects.file.get("path") == "big.py", timeout=30)
        colours = app.editor.highlighter
        with Clock() as opening:
            app.editor.open("p1", "big.py")
            wait_until(lambda: app.editor.editing, timeout=30)
        document = app.editor._document
        assert document.firstBlock().userState() >= 0  # the screen coloured at once, on opening
        note("редактор: открыть 1 МБ (текст в TextEdit, экран в цвете)", opening.s * 1000, "мс", 3000)
        with Clock() as whole:
            wait_until(lambda: colours._revision == document.revision() and not colours._pending, timeout=60)
        note("редактор: 1 МБ — разбор всего текста в фоне", whole.s * 1000, "мс", 10000)

        # the middle of the file: its colours when the screen gets there, from the lexing already made
        body = quick.window.findChild(QQuickItem, "editorText")
        middle = text.index(f"def total_{text.count('def total_') // 2}(")
        line = text.count("\n", 0, middle)
        with Clock() as scrolled:
            body.setProperty("cursorPosition", middle)
            wait_until(lambda: document.findBlockByNumber(line).userState() == colours._marks[line], timeout=10)
        note("редактор: 1 МБ — прыжок в середину, экран в цвете", scrolled.s * 1000, "мс", 300)
        wait_until(lambda: not colours._pending, timeout=10)
        note("редактор: 1 МБ — самый долгий кусок раскраски в потоке окна", max(colours.slices), "мс", 16)
        wanted = highlighter._style(palette.current().name).style_for_token(Keyword)["color"]
        red, green, blue = (int(wanted[i : i + 2], 16) for i in (0, 2, 4))
        shot = quick.window.grabWindow()
        corner = quick.window.findChild(QQuickItem, "codeEditor").mapToScene(QPointF(0, 0))  # the screen, not the text

        def like(x: int, y: int) -> bool:
            pixel = shot.pixelColor(x, y)
            return abs(pixel.red() - red) + abs(pixel.green() - green) + abs(pixel.blue() - blue) < 60

        left, top = int(corner.x()), int(corner.y())
        found = any(like(x, y) for y in range(top, top + 300, 2) for x in range(left, left + 600, 2))
        assert found, f"no pixel of #{wanted} (`def`) on screen in the middle of the file"

        quick.window.requestActivate()
        body.forceActiveFocus()
        body.setProperty("cursorPosition", len(text) // 2)
        spent = []
        for _ in range(60):
            began = time.perf_counter()
            for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QApplication.sendEvent(quick.window, QKeyEvent(kind, Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier, "x"))
            spent.append((time.perf_counter() - began) * 1000)
            QApplication.processEvents()
        note("редактор: 1 МБ — клавиша, медиана", statistics.median(spent), "мс", 16)
        note("редактор: 1 МБ — клавиша, худшая из 60", max(spent), "мс", 50)
        assert quick.warnings == []
    finally:
        quick.close()
