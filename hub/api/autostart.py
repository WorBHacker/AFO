"""/v1/autostart - starting by itself (Q8): the hub at Windows login, llama-swap with the hub; and where things are
(10.3): llama-swap's config and what starts it - chosen in the window, or the defaults - and LocalChat's own folders.
The desktop app only."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from common.paths import app_home

from .. import autostart, llama_config
from ..auth import APP_CLIENT, Client
from .deps import require_client

router = APIRouter(prefix="/v1")


class Change(BaseModel):
    login: bool | None = None
    llama_swap: bool | None = None
    llama_swap_config: str | None = Field(default=None, max_length=400)  # '' - back to the default
    llama_swap_start: str | None = Field(default=None, max_length=400)


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app changes how AFO starts")


async def _state(request: Request) -> dict:
    state = request.app.state
    script = autostart.llama_swap_script()
    config = llama_config.config_path()
    return {
        "login": autostart.login_enabled(state.run_store),
        "login_command": autostart.login_command(),
        "llama_swap": await state.db.acall(autostart.llama_swap_wanted),
        "llama_swap_script": str(script),
        "llama_swap_found": script.is_file(),
        "llama_swap_script_source": autostart.start_source(),
        "llama_swap_config": str(config),
        "llama_swap_config_found": config.is_file(),
        "llama_swap_config_source": llama_config.config_source(),
        "data_dir": str(state.settings.paths.root),
        "home": str(app_home()),
    }


def _checked(path: str, kinds: tuple[str, ...], what: str) -> str:
    """A chosen file: it exists and is of its kind ('' passes - the default comes back)."""
    if not path:
        return ""
    found = Path(path).expanduser()
    if not found.is_absolute() or not found.is_file():
        raise HTTPException(status_code=422, detail=f"{what}: нет такого файла — {path}")
    if found.suffix.lower() not in kinds:
        raise HTTPException(status_code=422, detail=f"{what}: нужен файл {' или '.join(kinds)}")
    return str(found)


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
    if body.llama_swap_config is not None:
        chosen = _checked(body.llama_swap_config, (".yaml", ".yml"), "Конфиг llama-swap")
        if chosen:
            try:
                llama_config.model_blocks(Path(chosen).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, llama_config.ConfigError) as exc:
                raise HTTPException(status_code=422, detail=f"Это не конфиг llama-swap: {exc}") from exc
        await state.db.acall(lambda conn: autostart.set_choice(conn, llama_config.CONFIG_SETTING, chosen))
        await state.gateway.sync_catalog()  # the models of that config
    if body.llama_swap_start is not None:
        chosen = _checked(body.llama_swap_start, (".exe", ".cmd", ".bat"), "Запуск llama-swap")
        await state.db.acall(lambda conn: autostart.set_choice(conn, autostart.START_SETTING, chosen))
    found = await _state(request)
    await state.bus.publish("autostart.changed", {"login": found["login"], "llama_swap": found["llama_swap"]})
    return found
