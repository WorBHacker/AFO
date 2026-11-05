"""A watch on the window's thread (2026-11-05, «прокрутка иногда лагает»): the software scene graph draws on the
window's thread, together with the window's Python - a frame waits whenever that thread is busy. A timer there says
«alive» every BEAT_MS; a thread of its own sees when it last did, notes where the window's thread is when that is
longer ago than the limit, and the stall is written to the window's log once it ends: how long, and where - its
Python stack, or only the event loop when Qt itself was busy (laying out, drawing). At most one line every QUIET_S:
the log tells what a lag was, it does not fill up."""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger("app.hitches")

BEAT_MS = 50
STALL_MS = 150
QUIET_S = 10.0
FRAMES = 6  # of the stack, from the innermost


def where(frame) -> str:
    """The innermost frames of a stack, «file:line function», innermost first."""
    if frame is None:
        return "unknown"
    stack = traceback.extract_stack(frame)[-FRAMES:]
    return " < ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(stack))


class HitchWatch(QObject):
    def __init__(self, parent: QObject | None = None, *, stall_ms: float = STALL_MS, quiet_s: float = QUIET_S):
        super().__init__(parent)
        self._thread_id = threading.get_ident()  # the window's thread: the one that makes the watch
        self._stall_s, self._quiet_s = stall_ms / 1000, quiet_s
        self._beat = time.monotonic()
        self._stall: tuple[float, str] | None = None  # (its last beat, where the thread was) while one goes on
        self._last_told = -1e9
        self.told: list[str] = []  # the lines written, for the tests
        self._timer = QTimer(self)
        self._timer.setInterval(BEAT_MS)
        self._timer.timeout.connect(self._alive)
        self._stop = threading.Event()
        self._lock = threading.Lock()  # a beat and the watch's look at it do not cross
        self._watcher = threading.Thread(target=self._watch, name="hitch-watch", daemon=True)

    def start(self) -> None:
        self._beat = time.monotonic()
        self._timer.start()
        self._watcher.start()

    def stop(self) -> None:
        self._stop.set()
        self._timer.stop()

    def _alive(self) -> None:
        with self._lock:
            now = time.monotonic()
            stall, self._stall, self._beat = self._stall, None, now
        if stall is not None and now - self._last_told >= self._quiet_s:
            self._last_told = now
            line = f"the window's thread was busy {(now - stall[0]) * 1000:.0f} ms: {stall[1]}"
            self.told.append(line)
            log.info("%s", line)

    def _watch(self) -> None:
        while not self._stop.wait(0.02):
            with self._lock:
                if self._stall is None and time.monotonic() - self._beat > self._stall_s:
                    self._stall = (self._beat, where(sys._current_frames().get(self._thread_id)))
