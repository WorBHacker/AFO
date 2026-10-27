"""WebSocket /v1/events: the live event stream (D31).

Protocol (JSON text frames):
- the client connects with `Authorization: Bearer <token>`; without a valid token the handshake is refused;
- the hub sends `{"type": "hub.hello", ...}` and then events matching the subscription (all, at first);
- the client may send `{"op": "subscribe", "topics": ["chat.*"], "since": 123}` at any time: from then on
  only matching events arrive, and stored events with id > since are sent first, so a reconnecting client
  misses nothing;
- `{"op": "ping"}` gets `{"type": "pong"}`.
If the client reads too slowly its queue overflows and the hub closes with code 1013: reconnect with `since`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from common import __version__

from ..auth import APP_CLIENT
from ..db import utc_now
from ..events import EventBus, Subscription
from .deps import client_from_headers

log = logging.getLogger("hub.ws")
router = APIRouter(prefix="/v1")

CLOSE_POLICY = 1008
CLOSE_TRY_AGAIN = 1013


@dataclass
class _Stream:
    """One connection's state. `lock` keeps a replay and live sends from interleaving."""

    subscription: Subscription
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
    stream = _Stream(bus.subscribe(("*",)))
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
    if window:
        websocket.app.state.app_windows = getattr(websocket.app.state, "app_windows", 0) + 1
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
            websocket.app.state.app_windows -= 1
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
            await websocket.send_json(event.to_json())
