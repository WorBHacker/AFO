"""Blocking calls (the hub's REST API) off the GUI thread, with their results delivered back on it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .hub_client import HubError

log = logging.getLogger("app.tasks")


class _Call(QObject):
    """Lives on the GUI thread; the worker emits its signals, its slots run the callbacks on the GUI thread."""

    finished = Signal(object)
    failed = Signal(object)  # the exception: a HubError may carry a code the caller acts on

    def __init__(self, owner: Background, on_done: Callable | None, on_error: Callable | None):
        super().__init__()
        self._owner = owner
        self._on_done = on_done
        self._on_error = on_error
        self.finished.connect(self._done)
        self.failed.connect(self._error)

    @Slot(object)
    def _done(self, result: Any) -> None:
        self._owner._release(self)
        if self._on_done is not None:
            self._on_done(result)

    @Slot(object)
    def _error(self, error: Exception) -> None:
        self._owner._release(self)
        if self._on_error is not None:
            self._on_error(error)
        else:
            self._owner.error.emit(str(error) or error.__class__.__name__)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], call: _Call):
        super().__init__()
        self._fn = fn
        self._call = call

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:
            if not isinstance(exc, HubError):  # a hub error is expected and shown; anything else is a bug
                log.exception("background call failed")
            self._call.failed.emit(exc)
            return
        self._call.finished.emit(result)


class Background(QObject):
    """`run(fn, on_done, on_error)`: fn runs on a pool thread. `on_error` gets the exception; a failure without
    its own handler goes to `error` as text."""

    error = Signal(str)

    def __init__(self, parent: QObject | None = None, threads: int = 4):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(threads)
        self._pending: set[_Call] = set()  # keeps each call object alive until its result is delivered

    def run(self, fn: Callable[[], Any], on_done: Callable | None = None, on_error: Callable | None = None) -> None:
        call = _Call(self, on_done, on_error)
        self._pending.add(call)
        self._pool.start(_Job(fn, call))

    def _release(self, call: _Call) -> None:
        self._pending.discard(call)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def wait(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(msecs)
