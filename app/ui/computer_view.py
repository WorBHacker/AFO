"""The agent at the desktop, seen by the person (stage 9.6, D41): while an autonomous run controls a window, the window
gets a frame around it and, above it, a bar - who acts, in what program, and «Стоп»; Ctrl+Alt+End stops it from
anywhere, and so does the tray's menu. The person's own input pauses the agent (the hub says so: a flash here).

The hub gives the window's place in real pixels (it is DPI-aware); Qt places windows in its own units, so the place
is turned into those for the screen it is on.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from ctypes import wintypes
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QAbstractNativeEventFilter, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication, QWindow

if TYPE_CHECKING:
    from .controller import Controller

log = logging.getLogger("app.ui.computer_view")

WM_HOTKEY = 0x0312
HOTKEY_ID = 0x4C43
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_END = 0x23
STOP_KEYS = "Ctrl+Alt+End"


def logical_rect(x: int, y: int, w: int, h: int, screens: list[tuple[int, int, int, int, float]]) -> list[int]:
    """A rectangle in real pixels as Qt places it: `screens` - each screen's (x, y, width, height) in Qt's units and
    its scale. Qt keeps a screen's top left corner in real pixels and scales the rest."""
    cx, cy = x + w // 2, y + h // 2
    for sx, sy, sw, sh, scale in screens:
        if sx <= cx < sx + sw * scale and sy <= cy < sy + sh * scale:
            return [round(sx + (x - sx) / scale), round(sy + (y - sy) / scale), round(w / scale), round(h / scale)]
    return [x, y, w, h]


def qt_screens() -> list[tuple[int, int, int, int, float]]:
    out = []
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        out.append((g.x(), g.y(), g.width(), g.height(), screen.devicePixelRatio()))
    return out


class ComputerView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._run = ""
        self._program = ""
        self._title = ""
        self._rect: list[int] = [0, 0, 0, 0]

    @Property(bool, notify=changed)
    def active(self) -> bool:
        return bool(self._run)

    @Property(list, notify=changed)
    def frame(self) -> list:
        return self._rect

    @Property(str, notify=changed)
    def text(self) -> str:
        return f"Агент управляет компьютером · {self._program}" if self._program else "Агент управляет компьютером"

    @Property(str, constant=True)
    def stopKeys(self) -> str:
        return STOP_KEYS

    @Slot()
    def stop(self) -> None:
        """«Стоп»: the run is cancelled; the frame goes when the hub says the hold is released."""
        run = self._run
        if not run or self.app.client is None:
            return
        self.app.flash("Агент остановлен: он больше ничего не нажмёт")
        self.app._call(lambda: self.app.client.cancel_run(run), None, lambda e: self.app.show_error(str(e)))

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "computer.control":
            window = payload.get("window") or {}
            rect = window.get("rect") or [0, 0, 0, 0]
            self._run = str(payload.get("run_id") or "")
            self._program = str(payload.get("program") or "")
            self._title = str(window.get("title") or "")
            self._rect = logical_rect(*[int(v) for v in rect[:4]], qt_screens())
            self.changed.emit()
        elif kind == "computer.released" and payload.get("run_id") == self._run:
            self._run, self._program, self._title = "", "", ""
            self.changed.emit()
        elif kind == "computer.paused":
            self.app.flash("Агент ждёт: вы работаете за компьютером — он продолжит, когда вы отпустите мышь")


class StopKey(QAbstractNativeEventFilter):
    """Ctrl+Alt+End from anywhere stops the agent - registered only while it controls the computer."""

    def __init__(self, hwnd: int, pressed: Callable[[], None]):
        super().__init__()
        self.hwnd = hwnd
        self._pressed = pressed
        self._on = False

    def set_on(self, on: bool) -> None:
        if on == self._on:
            return
        user32 = ctypes.windll.user32
        if on:
            if not user32.RegisterHotKey(self.hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_END):
                log.warning("%s is taken by another program: «Стоп» works from the frame and the tray", STOP_KEYS)
                return
        else:
            user32.UnregisterHotKey(self.hwnd, HOTKEY_ID)
        self._on = on

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
            self._pressed()
            return True, 0
        return False, 0


def install_stop_key(app: QGuiApplication, window: QWindow, view: ComputerView) -> StopKey | None:
    if sys.platform != "win32":
        return None
    key = StopKey(int(window.winId()), view.stop)
    app.installNativeEventFilter(key)
    view.changed.connect(lambda: key.set_on(view.active))
    return key
