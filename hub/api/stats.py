"""/v1/stats - what the hub has learnt about its models and its spending (stage 6, D11, D12). Open to every client."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from .. import stats
from ..auth import Client
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/stats")
async def get_stats(request: Request, client: Annotated[Client, Depends(require_client)], days: int = 30) -> dict:
    span = max(1, min(days, 365))

    def read(conn):
        return {"days": span, "models": stats.model_stats(conn, days=span), "spending": stats.spending(conn)}

    return await request.app.state.db.acall(read)
