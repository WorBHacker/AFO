"""/v1/support - «Проверить систему» and the logs for support (stage 10.6, D37; hub/diagnostics.py), and what the hub
keeps on disk with its limits (D30, hub/retention.py). The desktop app only: the check reads the computer, the report
gathers its logs.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import diagnostics, retention
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/support")


@router.get("/check")
async def check(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return {"lines": await diagnostics.check(request.app.state)}


@router.post("/report")
async def report(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    state = request.app.state
    lines = await diagnostics.check(state)
    path = await asyncio.to_thread(diagnostics.report, state.settings.paths, lines)
    return {"path": str(path), "folder": str(path.parent), "lines": lines}


class Limits(BaseModel):
    days: int
    max_gb: float


async def _kept(request: Request) -> dict:
    root = request.app.state.settings.paths.root
    limits = await request.app.state.db.acall(retention.current)
    return {**limits, "usage": await asyncio.to_thread(retention.usage, root)}


@router.get("/retention")
async def kept(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """How long and how much the hub keeps (D30), and what each kind takes now."""
    return await _kept(request)


@router.put("/retention")
async def set_kept(body: Limits, request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        await request.app.state.db.acall(lambda conn: retention.save(conn, body.days, body.max_gb))
    except retention.RetentionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _kept(request)


@router.post("/retention/prune")
async def prune(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """«Очистить сейчас»: everything past its term or over the size goes now."""
    removed = await request.app.state.keeper.prune()
    return {**await _kept(request), "removed": removed}
