"""One AFO window (43.1): the first `python -m app` holds data/app.lock and listens on a local socket named by its
data folder; a second one - a shortcut, Windows' login while AFO is in the tray - only asks it to show its window (or,
from the login, nothing) and leaves: no second window, no second tray icon.

Named by the data folder: a test's folder, or a portable AFO, is an AFO of its own. The lock decides who is first
(two starts at once: one of them); the socket carries the word. Only the user's own programs may connect.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from hub.singleton import AlreadyRunning, InstanceLock

log = logging.getLogger("app.single")

ASK_S = 15.0  # the first may hold the lock and not listen yet: it is starting


def server_name(root: Path) -> str:
    return "AFO-" + hashlib.sha256(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:16]


class Single(QObject):
    """The first AFO's ear: what a second start asks comes as `asked` (in the event loop)."""

    asked = Signal(str)

    def __init__(self, root: Path, lock: InstanceLock, parent: QObject | None = None):
        super().__init__(parent)
        self.lock = lock  # held while this AFO lives: the system lets it go when the process ends
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        name = server_name(root)
        if not self.server.listen(name):  # left by an AFO that crashed (not on Windows: its pipes go with it)
            QLocalServer.removeServer(name)
            self.server.listen(name)
        self.server.newConnection.connect(self._came)
        log.info("one window: waits for a second start on %s", name)

    def _came(self) -> None:
        while (socket := self.server.nextPendingConnection()) is not None:
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        word = bytes(socket.readAll().data()).decode("utf-8", errors="replace").strip()
        if word:
            log.info("a second start asks: %s", word)
            self.asked.emit(word)

    def close(self) -> None:
        self.server.close()
        self.lock.release()


def first(root: Path) -> Single | None:
    """This AFO's Single when it is the first; None - another runs already."""
    lock = InstanceLock(root / "app.lock")
    try:
        lock.acquire()
    except AlreadyRunning:
        return None
    return Single(root, lock)


def ask_running(root: Path, word: str) -> bool:
    """Tells the running AFO `word` ("show", "tray"); False - it did not answer in ASK_S."""
    deadline = time.monotonic() + ASK_S
    while True:
        socket = QLocalSocket()
        socket.connectToServer(server_name(root))
        if socket.waitForConnected(1000):
            socket.write((word + "\n").encode("utf-8"))
            socket.flush()
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                socket.waitForDisconnected(1000)
            log.info("AFO runs already: asked it to %s", word)
            return True
        if time.monotonic() > deadline:
            log.warning("AFO holds its lock but does not answer: %s", socket.errorString())
            return False
        time.sleep(0.2)
