"""/v1/secrets - API keys in the OS secret store (D13). Values only go in; the API says whether a key is set.

Changing a key refreshes the model catalog, so Claude models appear or disappear with their key.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import APP_CLIENT, Client
from ..secrets import KNOWN_SECRETS
from .deps import require_client

router = APIRouter(prefix="/v1")


class SecretValue(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


def _known(name: str) -> str:
    if name not in KNOWN_SECRETS:
        raise HTTPException(status_code=404, detail=f"unknown secret {name!r}; known: {sorted(KNOWN_SECRETS)}")
    return name


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change secrets")


@router.get("/secrets")
async def list_secrets(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    store = request.app.state.secrets
    return {
        "secrets": [
            {"name": name, "label": label, "set": bool(store.get(name))} for name, label in KNOWN_SECRETS.items()
        ]
    }


@router.put("/secrets/{name}")
async def set_secret(
    name: str, body: SecretValue, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    request.app.state.secrets.set(_known(name), body.value.strip())
    return await _changed(request, name, is_set=True)


@router.delete("/secrets/{name}")
async def delete_secret(name: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    request.app.state.secrets.delete(_known(name))
    return await _changed(request, name, is_set=False)


async def _changed(request: Request, name: str, *, is_set: bool) -> dict:
    state = request.app.state
    await state.bus.publish("secrets.changed", {"name": name, "set": is_set})  # never the value
    catalog = await state.gateway.sync_catalog()
    await state.bus.publish("models.synced", catalog)
    return {"name": name, "set": is_set, "catalog": catalog}
