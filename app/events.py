"""The hub's live events (WebSocket /v1/events) on a background thread, handed to the GUI as Qt signals.

The thread reconnects on its own. After a reconnect it asks for the stored events it missed (`since`), so a
finished answer or an error is never lost; streamed deltas are not stored, which is why the window reloads the
open chat when the connection comes back.
"""

from __future__ import annotations

import json
import logging
import threading

from PySide6.QtCore import QThread, Signal
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

log = logging.getLogger("app.events")

TOPICS = [
    "chat.*",
    "models.*",
    "system.*",
    "secrets.*",
    "budget.*",
    "project.*",
    "processes.*",
    "process.*",
    "port.*",
    "git.*",
    "terminal.*",
    "card.*",
    "note.*",
    "links.*",
    "link.*",
    "hub.*",
]
RECONNECT_S = 2.0


class EventThread(QThread):
    event = Signal(dict)
    connected = Signal(bool)

    def __init__(self, url: str, token: str, topics: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._url = url
        self._token = token
        self._topics = topics or TOPICS
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        self.wait(5000)

    def run(self) -> None:
        last_id = 0  # the newest stored event already handed over; replays up to it are skipped
        while not self._stop.is_set():
            try:
                with connect(
                    self._url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                    open_timeout=5,
                    close_timeout=1,
                    max_size=None,
                ) as ws:
                    hello = json.loads(ws.recv(timeout=5))
                    last_id = last_id or int(hello.get("payload", {}).get("last_event_id", 0))
                    ws.send(json.dumps({"op": "subscribe", "topics": self._topics, "since": last_id}))
                    self.connected.emit(True)
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=0.5)
                        except TimeoutError:
                            continue
                        event = json.loads(raw)
                        event_id = event.get("id")
                        if event_id is not None:
                            if event_id <= last_id:
                                continue  # seen live before the subscription replayed it
                            last_id = event_id
                        if event.get("type") in ("hub.subscribed", "pong"):
                            continue
                        self.event.emit(event)
            except (OSError, TimeoutError, ValueError, WebSocketException) as exc:
                log.info("events: %s", exc.__class__.__name__ if not str(exc) else exc)
            if self._stop.is_set():
                break
            self.connected.emit(False)
            self._stop.wait(RECONNECT_S)
