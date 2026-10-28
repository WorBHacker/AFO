"""/v1/support - «Проверить систему» and the logs for support (stage 10.6, D37; hub/diagnostics.py). The desktop app
only: the check reads the computer, the report gathers its logs.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from .. import diagnostics
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
