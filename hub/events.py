"""The event bus: every event is written to the `events` table and handed to live subscribers.

Subscribers (the WebSocket clients) pick event types with patterns like 'chat.*' or '*'. A client that
reconnects asks for the events after the last id it saw and gets them from the table, so nothing is
lost while it was away. A subscriber that cannot keep up is cut off (its queue overflows) and can
reconnect the same way.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from .db import Database, utc_now

log = logging.getLogger("hub.events")

QUEUE_SIZE = 1000
REPLAY_LIMIT = 5000


@dataclass(frozen=True)
class Event:
    id: int | None  # None for an event that was not stored
    ts: str
    type: str
    payload: dict[str, Any]
    project_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "type": self.type, "project_id": self.project_id, "payload": self.payload}


def matches(event_type: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(event_type, pattern) for pattern in patterns)


@dataclass(eq=False)
class Subscription:
    patterns: tuple[str, ...]
    queue: asyncio.Queue[Event] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    overflowed: bool = False

    def offer(self, event: Event) -> None:
        if self.overflowed or not matches(event.type, self.patterns):
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True


class EventBus:
    def __init__(self, db: Database):
        self._db = db
        self._subscribers: set[Subscription] = set()

    async def publish(
        self, event_type: str, payload: dict[str, Any] | None = None, *, project_id: str | None = None, persist=True
    ) -> Event:
        payload = payload or {}
        ts = utc_now()
        event_id = None
        if persist:
            text = json.dumps(payload, ensure_ascii=False)
            event_id = await self._db.acall(
                lambda conn: (
                    conn.execute(
                        "INSERT INTO events (ts, type, project_id, payload) VALUES (?, ?, ?, ?)",
                        (ts, event_type, project_id, text),
                    ).lastrowid
                )
            )
        event = Event(event_id, ts, event_type, payload, project_id)
        for subscriber in list(self._subscribers):
            subscriber.offer(event)
        return event

    def subscribe(self, patterns: tuple[str, ...] = ("*",)) -> Subscription:
        subscription = Subscription(patterns)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    async def stored_after(self, since_id: int, patterns: tuple[str, ...]) -> list[Event]:
        rows = await self._db.acall(
            lambda conn: conn.execute(
                "SELECT id, ts, type, project_id, payload FROM events WHERE id > ? ORDER BY id LIMIT ?",
                (since_id, REPLAY_LIMIT),
            ).fetchall()
        )
        events = [Event(r["id"], r["ts"], r["type"], json.loads(r["payload"]), r["project_id"]) for r in rows]
        return [e for e in events if matches(e.type, patterns)]

    async def last_id(self) -> int:
        return await self._db.acall(lambda conn: conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
