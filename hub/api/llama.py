"""/v1/llama - a local model's launch settings in llama-swap's config.yaml (stage 8.5, D39, Q25; hub/llama_config.py).
The desktop app's alone: it decides how the user's models start.

GET lists the models with their settings, files and a memory estimate's inputs; PUT changes one model (a copy of the
file first, then the check that llama-swap reads it again - or the copy back); POST .../trial loads the model and asks
it one short question, and puts the copy back when it does not start; POST /restore takes back any copy.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from common.gguf import GGUFError, model_info
from common.i18n import tr
from common.llama_memory import estimate

from .. import llama_config, store
from ..auth import APP_CLIENT, Client
from .deps import require_client
from .model_settings import run_check

log = logging.getLogger("hub.llama")
router = APIRouter(prefix="/v1/llama")
PROVIDER = "llama-swap"


def require_app(client: Annotated[Client, Depends(require_client)]) -> Client:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app changes how local models start")
    return client


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"llama-swap's config is not readable: {exc}") from exc


def _files(view: dict[str, Any]) -> dict[str, Any]:
    """What the window's memory estimate needs from the model's files (their headers only)."""
    out: dict[str, Any] = {}
    if view.get("file"):
        try:
            out["info"] = model_info(Path(view["file"]))
        except (OSError, GGUFError) as exc:
            out["file_error"] = str(exc)
    if view.get("mmproj"):
        try:
            out["mmproj_bytes"] = Path(view["mmproj"]).stat().st_size
        except OSError:
            out["mmproj_bytes"] = 0
    return out


async def _listing(request: Request) -> dict[str, Any]:
    path = llama_config.config_path()
    text = _read(path)
    try:
        ids = list(llama_config.model_blocks(text))
    except llama_config.ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    views = []
    for model_id in ids:  # a block the hub cannot read is said on its own row; the others still show (2026-10-28)
        try:
            views.append(llama_config.model_view(text, model_id))
        except llama_config.ConfigError as exc:
            views.append({"id": model_id, "name": model_id, "engine": "other", "editable": False, "ttl": 0,
                          "command": "", "problem": str(exc)})  # fmt: skip
    for view in views:
        view.update(await asyncio.to_thread(_files, view))
    status = request.app.state.status.latest or {}
    return {"path": str(path), "models": views, "vram_total_mib": (status.get("gpu") or {}).get("vram_total_mib"),
            "vram_reserve_gib": request.app.state.installer.reserve["vram_gib"],  # the user's (38.3)
            "backups": [p.name for p in llama_config.backups(path)]}  # fmt: skip


@router.get("/models")
async def models(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return await _listing(request)


async def _verify(request: Request, ids: list[str], context: tuple[str, int] | None) -> tuple[bool, str]:
    return await llama_config.reloaded(request.app.state.settings.llama_swap_url, ids, context)


class Change(BaseModel):
    changes: dict[str, Any]


@router.put("/models/{model_id}")
async def change(request: Request, model_id: str, body: Change, client: Annotated[Client, Depends(require_app)]):
    path = llama_config.config_path()
    async with llama_config.writing():  # 34.4: an install or the tuning does not write in between
        text = _read(path)
        try:
            new = llama_config.edit(text, model_id, body.changes)
        except llama_config.ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if new == text:
            return {"changed": False, "verified": True, "backup": "", **await _listing(request)}
        backup = await asyncio.to_thread(llama_config.write, path, new)
        ids = list(llama_config.model_blocks(new))
        context = (model_id, body.changes["context"]) if body.changes.get("context") else None
        verified, problem = await _verify(request, ids, context)
        if not verified and problem != tr(llama_config.NOT_ANSWERING):  # it read the file, something is wrong: undo
            await asyncio.to_thread(llama_config.restore, path, backup)
            raise HTTPException(status_code=502, detail=tr("llama-swap не принял правку ({problem}) — конфиг возвращён",
                                                           problem=problem))  # fmt: skip
    request.app.state.llama_trial = (model_id, backup)  # a failed trial of this model puts this copy back
    await request.app.state.bus.publish("llama.config", {"model_id": model_id, "changes": body.changes,
                                                         "backup": backup.name, "verified": verified})  # fmt: skip
    await request.app.state.gateway.sync_catalog()  # the catalog shows the new context
    return {"changed": True, "verified": verified, "problem": problem, "backup": backup.name,
            **await _listing(request)}  # fmt: skip


@router.post("/models/{model_id}/trial")
async def trial(request: Request, model_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    """The model started with its settings and asked one short question. When it does not answer and its settings
    were changed by the last write, that file's copy comes back (Q25). When it answers with settings the memory
    estimate says do not fit, `over` says so: the video card's driver lent it shared memory, it is several times
    slower (stage 28)."""
    state = request.app.state
    model = await state.db.acall(lambda conn: store.get_model(conn, f"{PROVIDER}/{model_id}"))
    if model is None or not model["enabled"]:
        raise HTTPException(status_code=404, detail=f"llama-swap does not offer {model_id} now")
    result = await run_check(state, model, client)
    if result.get("ok") and (judged := await _estimate(request, model_id)) and judged["verdict"] == "over":
        result["over"] = judged["text"]  # it answered from the shared memory the driver lent it: slower (stage 28)
    last = getattr(state, "llama_trial", None)
    if not result.get("ok") and last and last[0] == model_id:
        path = llama_config.config_path()
        async with llama_config.writing():
            await asyncio.to_thread(llama_config.restore, path, last[1])
            state.llama_trial = None
            await _verify(request, list(llama_config.model_blocks(_read(path))), None)
        await state.bus.publish("llama.config", {"model_id": model_id, "restored": last[1].name})
        await state.gateway.sync_catalog()
        result["restored"] = last[1].name
    return result


async def _estimate(request: Request, model_id: str) -> dict[str, Any] | None:
    """The window's memory estimate (8.5) of the model's settings as the config has them now; None when there is
    nothing to estimate - not llama.cpp's, its file not read, no video card known."""
    try:
        view = llama_config.model_view(_read(llama_config.config_path()), model_id)
    except HTTPException, llama_config.ConfigError:
        return None
    view.update(await asyncio.to_thread(_files, view))
    total = ((request.app.state.status.latest or {}).get("gpu") or {}).get("vram_total_mib")
    if not view.get("editable") or not view.get("info") or not total:
        return None
    return estimate(view["info"], context=view.get("context") or 4096, cache_type=view.get("cache_type") or "f16",
                    gpu_layers=view.get("gpu_layers") or "all", mmproj_bytes=view.get("mmproj_bytes") or 0,
                    mmproj_on_gpu=bool(view.get("mmproj_on_gpu")), vram_total_mib=total,
                    reserve_gib=request.app.state.installer.reserve["vram_gib"])  # fmt: skip


class Restore(BaseModel):
    backup: str


@router.post("/restore")
async def restore(request: Request, body: Restore, client: Annotated[Client, Depends(require_app)]) -> dict:
    path = llama_config.config_path()
    found = next((p for p in llama_config.backups(path) if p.name == body.backup), None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no copy {body.backup!r} beside the config")
    async with llama_config.writing():
        await asyncio.to_thread(llama_config.write, path, found.read_text(encoding="utf-8"))  # the current one kept
        verified, problem = await _verify(request, list(llama_config.model_blocks(_read(path))), None)
    await request.app.state.bus.publish("llama.config", {"restored": body.backup, "verified": verified})
    await request.app.state.gateway.sync_catalog()
    return {"verified": verified, "problem": problem, **await _listing(request)}
