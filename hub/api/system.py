"""/v1/health (open, so a client can tell whether a hub is running) and /v1/info (needs a token)."""

from __future__ import annotations

import os
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from common import __version__

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
