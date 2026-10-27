"""/v1/autostart - starting by itself (Q8): the hub at Windows login, llama-swap with the hub. The desktop app only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import autostart
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


class Change(BaseModel):
    login: bool | None = None
    llama_swap: bool | None = None


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app changes how LocalChat starts")


async def _state(request: Request) -> dict:
    state = request.app.state
    script = autostart.llama_swap_script()
    return {
        "login": autostart.login_enabled(state.run_store),
        "login_command": autostart.login_command(),
        "llama_swap": await state.db.acall(autostart.llama_swap_wanted),
        "llama_swap_script": str(script),
        "llama_swap_found": script.is_file(),
    }


@router.get("/autostart")
async def get_autostart(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    return await _state(request)


@router.put("/autostart")
async def set_autostart(body: Change, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    state = request.app.state
    if body.login is not None:
        autostart.set_login(state.run_store, body.login)
    if body.llama_swap is not None:
        await state.db.acall(lambda conn: autostart.set_llama_swap_wanted(conn, body.llama_swap))
    found = await _state(request)
    await state.bus.publish("autostart.changed", {"login": found["login"], "llama_swap": found["llama_swap"]})
    return found
