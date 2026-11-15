"""/v1/models - the model catalog (D5-D7); /v1/models/sync - refresh it from the providers."""

from __future__ import annotations

import asyncio
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


LET_GO_S = 15.0  # «Выгрузить»: how long the stopped holder is waited for before the second unload (48.9г)


@router.post("/models/unload")
async def unload_local(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Frees the GPU - «the card is needed by me now» (48.9г): through the card's owner. Whoever holds it stops - the
    Studio's job as by ✕ (its queue waits for the person), an install's trial put off, tuning stopped - and the card is
    the person's until their next action; llama-swap stops whatever local model it runs. Once the holder has let go,
    llama-swap is asked again: a look of the stopped one's may have loaded its model in between."""
    state = request.app.state
    url = f"{state.settings.llama_swap_url.rstrip('/')}/api/models/unload"
    held = bool(state.card.holder)
    state.visual.hold()
    state.card.give_away()
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            response = await http.post(url)
            response.raise_for_status()
            if held:
                await state.card.until_let_go(LET_GO_S)
                await asyncio.sleep(0.5)  # its last look answered
                await http.post(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"llama-swap did not unload: {exc}") from exc
    await request.app.state.bus.publish("models.unloaded", {"by": client.name})
    await request.app.state.status.sample()  # clients see the freed VRAM right away
    return {"unloaded": True}
