"""WebSocket /v1/events: the live event stream (D31).

Protocol (JSON text frames):
- the client connects with `Authorization: Bearer <token>`; without a valid token the handshake is refused;
- the hub sends `{"type": "hub.hello", ...}` and then events matching the subscription (all, at first);
- the client may send `{"op": "subscribe", "topics": ["chat.*"], "since": 123}` at any time: from then on
  only matching events arrive, and stored events with id > since are sent first, so a reconnecting client
  misses nothing;
- `{"op": "ping"}` gets `{"type": "pong"}`.
If the client reads too slowly its queue overflows and the hub closes with code 1013: reconnect with `since`.

The window gets every event. Another client (D31, D46) gets the hub's and the projects' state (OUTSIDE_EVENTS) - a
cloud one only of the projects that allow the cloud - and the events of the conversations it began; never another
conversation's text, an approval or a terminal's output.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from common import __version__

from .. import store
from ..auth import APP_CLIENT, Client
from ..db import utc_now
from ..events import Event, EventBus, Subscription, matches
from .deps import client_from_headers

log = logging.getLogger("hub.ws")
router = APIRouter(prefix="/v1")

CLOSE_POLICY = 1008
CLOSE_TRY_AGAIN = 1013
# the window's unsaved files (D47) outlive its last events connection this long: a window that reconnects (after
# 1013, or a hub that stopped for a moment) says them again; one gone for good took its edits along
UNSAVED_KEPT_S = 30.0
# what a client other than the window follows besides its own conversations: state, never text
OUTSIDE_EVENTS = ("hub.*", "project.*", "git.head", "processes.changed", "process.stopped", "port.*", "models.*",
                  "llama_swap.started", "claude_code.limit", "claude_code.limits")  # fmt: skip


class _Gate:
    """Whether this client gets an event; what it asks the database for is kept for the connection's life (a
    project's cloud flag until the project changes)."""

    def __init__(self, websocket: WebSocket, client: Client):
        self._db = websocket.app.state.db
        self._client = client
        self._owners: dict[str, str | None] = {}
        self._cloud: dict[str, bool] = {}

    async def allows(self, event: Event) -> bool:
        if self._client.name == APP_CLIENT:
            return True
        conversation = event.payload.get("conversation_id") or event.payload.get("run_id")
        if isinstance(conversation, str) and conversation:  # a conversation's event: its own client's only
            if conversation not in self._owners:
                self._owners[conversation] = await self._db.acall(
                    lambda conn: store.conversation_owner(conn, conversation)
                )
            return self._owners[conversation] == self._client.id
        if not matches(event.type, OUTSIDE_EVENTS):
            return False
        if event.type in ("project.changed", "project.added", "project.removed"):
            self._cloud.pop(event.project_id or event.payload.get("id") or "", None)
        project = event.project_id or event.payload.get("id") if event.type.startswith("project.") else event.project_id
        if not self._client.cloud or not project:
            return True
        if project not in self._cloud:
            row = await self._db.acall(
                lambda conn: conn.execute("SELECT cloud_ok FROM projects WHERE id = ?", (project,)).fetchone()
            )
            self._cloud[project] = bool(row and row["cloud_ok"])
        return self._cloud[project]


@dataclass
class _Stream:
    """One connection's state. `lock` keeps a replay and live sends from interleaving."""

    subscription: Subscription
    gate: _Gate
    skip_until: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    client = await client_from_headers(websocket)
    if client is None:
        log.warning("refused /v1/events: no valid token")
        await websocket.close(code=CLOSE_POLICY)
        return
    await websocket.accept()
    bus: EventBus = websocket.app.state.bus
    stream = _Stream(bus.subscribe(("*",)), _Gate(websocket, client))
    await websocket.send_json(
        {
            "type": "hub.hello",
            "payload": {
                "version": __version__,
                "client": client.name,
                "server_time": utc_now(),
                "last_event_id": await bus.last_id(),
            },
        }
    )
    log.info("events: client %s connected", client.name)
    window = client.name == APP_CLIENT  # the desktop app: it draws the agent's frame and «stop» (D41)
    state = websocket.app.state
    if window:
        state.app_windows = getattr(state, "app_windows", 0) + 1
        if (forgetting := getattr(state, "forget_unsaved", None)) is not None:
            forgetting.cancel()  # a window is back: its list stays (it says it again)
    sender = asyncio.create_task(_send_live(websocket, stream))
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                await websocket.send_json({"type": "error", "payload": {"detail": "frames must be JSON"}})
                continue
            op = message.get("op") if isinstance(message, dict) else None
            if op == "ping":
                await websocket.send_json({"type": "pong"})
            elif op == "subscribe":
                await _resubscribe(websocket, bus, stream, message)
            else:
                await websocket.send_json({"type": "error", "payload": {"detail": f"unknown op {op!r}"}})
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        bus.unsubscribe(stream.subscription)
        if window:
            state.app_windows -= 1
            if not state.app_windows:
                forget = state.approvals.unsaved.forget
                state.forget_unsaved = asyncio.get_running_loop().call_later(UNSAVED_KEPT_S, forget)
        log.info("events: client %s disconnected", client.name)


async def _resubscribe(websocket: WebSocket, bus: EventBus, stream: _Stream, message: dict) -> None:
    topics = tuple(str(t) for t in (message.get("topics") or ["*"]))
    since = message.get("since")
    async with stream.lock:
        bus.unsubscribe(stream.subscription)
        # subscribe before reading stored events: an event published in between waits in the queue
        # and is skipped there when the replay already sent it
        stream.subscription = bus.subscribe(topics)
        sent_up_to = 0
        if isinstance(since, int) and since >= 0:
            for event in await bus.stored_after(since, topics):
                if await stream.gate.allows(event):
                    await websocket.send_json(event.to_json())
                sent_up_to = event.id or sent_up_to
        stream.skip_until = sent_up_to
        await websocket.send_json(
            {"type": "hub.subscribed", "payload": {"topics": list(topics), "replayed_up_to": sent_up_to}}
        )


async def _send_live(websocket: WebSocket, stream: _Stream) -> None:
    while True:
        subscription = stream.subscription
        try:
            event = await asyncio.wait_for(subscription.queue.get(), timeout=0.5)
        except TimeoutError:
            if subscription.overflowed:
                await websocket.close(code=CLOSE_TRY_AGAIN)
                return
            continue
        async with stream.lock:
            if subscription is not stream.subscription:
                continue  # left over from a subscription that was just replaced
            if event.id is not None and event.id <= stream.skip_until:
                continue  # already sent by the replay
            if await stream.gate.allows(event):
                await websocket.send_json(event.to_json())
