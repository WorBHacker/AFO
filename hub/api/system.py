"""/v1/health (open, so a client can tell whether a hub is running) and /v1/info (needs a token)."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from common import __version__

from .. import store
from ..auth import APP_CLIENT, Client
from ..db import current_version
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/health")
async def health(request: Request) -> dict:
    return {
        "status": "ok",
        "service": "localchat-hub",
        "version": __version__,
        "uptime_s": round(time.monotonic() - request.app.state.started_monotonic, 1),
    }


@router.get("/info")
async def info(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    state = request.app.state
    return {
        "version": __version__,
        "pid": os.getpid(),
        "data_dir": str(state.settings.paths.root),
        "schema_version": await state.db.acall(current_version),
        "last_event_id": await state.bus.last_id(),
        "client": {"name": client.name, "cloud": client.cloud},
    }


@router.get("/usage")
async def usage(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # "today" is the local day
    return await request.app.state.db.acall(lambda conn: store.usage_totals(conn, today_since=since))


@router.get("/feed")
async def feed(
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    limit: int = 100,
    before: int | None = None,
    project_id: str | None = None,
    types: str = "",
) -> dict:
    """The event log, newest first (D29): what happened, for the window's feed. `types` - a comma list of types or
    prefixes ("terminal.*"); `before` - an event id, for the next page."""
    limit = max(1, min(limit, 500))
    where, args = ["1 = 1"], []
    if before is not None:
        where.append("id < ?")
        args.append(before)
    if project_id:
        where.append("project_id = ?")
        args.append(project_id)
    wanted = [t.strip() for t in types.split(",") if t.strip()]
    if wanted:
        where.append("(" + " OR ".join("type LIKE ?" if t.endswith("*") else "type = ?" for t in wanted) + ")")
        args += [t[:-1] + "%" if t.endswith("*") else t for t in wanted]
    sql = f"SELECT id, ts, type, project_id, payload FROM events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?"
    rows = await request.app.state.db.acall(lambda conn: conn.execute(sql, [*args, limit]).fetchall())
    events = [
        {
            "id": r["id"],
            "ts": r["ts"],
            "type": r["type"],
            "project_id": r["project_id"],
            "payload": json.loads(r["payload"]),
        }
        for r in rows
    ]
    return {"events": events, "more": len(events) == limit}


@router.get("/status")
async def status(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """GPU, memory and the loaded local model (the latest sample, or a fresh one), plus busy chats.
    The same picture arrives as `system.status` events whenever it changes."""
    monitor = request.app.state.status
    sample = monitor.latest or await monitor.sample()
    return {**sample, "busy": request.app.state.chat.busy}


@router.post("/shutdown", status_code=202)
async def shutdown(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Graceful stop: the hub finishes requests, publishes hub.stopping and removes hub.json."""
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may stop the hub")
    stop = request.app.state.request_shutdown
    if stop is None:
        raise HTTPException(status_code=503, detail="this hub was not started by `python -m hub`")
    await request.app.state.bus.publish("hub.shutdown_requested", {"by": client.name})
    stop()
    return {"status": "stopping"}
