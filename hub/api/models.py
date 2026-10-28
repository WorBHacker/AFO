"""/v1/models - the model catalog (D5-D7); /v1/models/sync - refresh it from the providers."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import model_settings, store
from ..auth import Client
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/models")
async def models(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    rows = await request.app.state.db.acall(store.list_models)
    settings = await request.app.state.db.acall(model_settings.all_current)
    gateway = request.app.state.gateway
    out = []
    for row in rows:
        accepts = gateway.accepts(row["id"])  # which of the answer's parameters it takes (D39): the window shows those
        item = {**row, "accepts": sorted(accepts)}
        if "refusal_vector" in accepts:  # how it is set: a new task's switch starts from it (on unless set off)
            params = settings.get(row["id"], (0, {}))[1].get("params") or {}
            item["refusal_vector"] = params.get("refusal_vector", True)
        out.append(item)
    return {"providers": gateway.providers, "models": out}


@router.post("/models/sync")
async def sync_models(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    result = await request.app.state.gateway.sync_catalog()
    await request.app.state.bus.publish("models.synced", result)
    rows = await request.app.state.db.acall(store.list_models)
    return {**result, "models": rows}


@router.post("/models/unload")
async def unload_local(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Frees the GPU: llama-swap stops whatever local model it runs (it loads one again on the next message)."""
    url = f"{request.app.state.settings.llama_swap_url.rstrip('/')}/api/models/unload"
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            response = await http.post(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"llama-swap did not unload: {exc}") from exc
    await request.app.state.bus.publish("models.unloaded", {"by": client.name})
    await request.app.state.status.sample()  # clients see the freed VRAM right away
    return {"unloaded": True}
