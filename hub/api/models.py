"""/v1/models - the model catalog (D5-D7); /v1/models/sync - refresh it from the providers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from .. import store
from ..auth import Client
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/models")
async def models(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    rows = await request.app.state.db.acall(store.list_models)
    return {"providers": request.app.state.gateway.providers, "models": rows}


@router.post("/models/sync")
async def sync_models(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    result = await request.app.state.gateway.sync_catalog()
    await request.app.state.bus.publish("models.synced", result)
    rows = await request.app.state.db.acall(store.list_models)
    return {**result, "models": rows}
