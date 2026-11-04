"""/v1/install - local models from Hugging Face into llama-swap (stage 10.1, D37; hub/model_install.py). The desktop
app's alone, like the launch settings (hub/api/llama.py): it decides what runs on the user's computer.

GET /system - this computer as an install sees it (video memory and RAM for a new model, the drives); GET /catalog -
the curated list with the variant recommended here; GET /search - Hugging Face at large; GET /repo/<a>/<b> - one
repository's variants judged. POST / starts an install; GET /jobs lists them; POST /jobs/<id>/<pause|resume|cancel>;
DELETE /jobs/<id> takes an unfinished one off the list (its files too); DELETE /models/<id> removes an installed model.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import hf, llama_config
from .. import model_install as mi
from ..auth import Client
from .llama import require_app

router = APIRouter(prefix="/v1/install")


def _installer(request: Request) -> mi.Installer:
    return request.app.state.installer


def _refusal(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", 422 if isinstance(exc, llama_config.ConfigError) else 409)
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/system")
async def system(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    installer = _installer(request)
    drives = await asyncio.to_thread(installer.drives)
    try:
        text = await asyncio.to_thread(mi.config_text)
        server = llama_config.server_command(text)
        problem = ""
    except (mi.InstallError, llama_config.ConfigError) as exc:
        server, problem = "", str(exc)
    home = mi.models_home()
    on = next((d for d in drives if str(home).upper().startswith(str(d["root"]).upper()[:2])), None)
    return {"space": installer.space(), "drives": [{**d, "folder": str(mi.models_root(d))} for d in drives],
            "server": server, "problem": problem, "folder": str(home),  # where a model goes unless told otherwise
            "home": {"folder": str(home), "letter": on["letter"] if on else "", "words": on.get("words", "") if on
                     else "", "kind": on.get("kind", "") if on else "", "free_gib": on["free_gib"] if on else None},
            "token": bool(installer.token()), "purposes": {k: tr(v) for k, v in mi.PURPOSES.items()}}  # fmt: skip


@router.get("/catalog")
async def catalog(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    return {"models": await _installer(request).catalog()}


@router.get("/search")
async def search(request: Request, q: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    if not q.strip():
        return {"models": []}
    try:
        return {"models": await _installer(request).search(q.strip()[:100])}
    except hf.HubError as exc:
        raise _refusal(exc) from exc


@router.get("/repo/{author}/{name}")
async def repository(request: Request, author: str, name: str, client: Annotated[Client, Depends(require_app)],
                     fresh: bool = False) -> dict:  # fmt: skip
    installer = _installer(request)
    repo = f"{author}/{name}"
    try:
        if fresh:
            await installer.repository(repo, fresh=True)
        entry = next((e for e in mi.curated() if e["repo"] == repo), None)
        return await installer.details(repo, entry)
    except (hf.HubError, mi.InstallError) as exc:
        raise _refusal(exc) from exc


class Install(BaseModel):
    repo: str = Field(min_length=3, max_length=200)
    quant: str = Field(min_length=1, max_length=40)
    projector: bool | None = None  # None: as the model is offered (the catalog's pictures, else none)
    drive: str = Field(default="", max_length=3)
    folder: str = Field(default="", max_length=400)
    name: str = Field(default="", max_length=120)


@router.post("")
async def install(request: Request, body: Install, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        return {"job": await _installer(request).install(body.model_dump(), client)}
    except (hf.HubError, mi.InstallError, llama_config.ConfigError) as exc:
        raise _refusal(exc) from exc


@router.get("/jobs")
async def jobs(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    installer = _installer(request)
    found = await request.app.state.db.acall(mi.jobs)
    for job in found:
        job["speed"] = round(installer.speed.get(job["id"], 0.0))
    return {"jobs": found}


@router.post("/jobs/{job_id}/{action}")
async def act(request: Request, job_id: str, action: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    installer = _installer(request)
    try:
        if action == "pause":
            return {"job": await installer.pause(job_id)}
        if action == "resume":
            return {"job": await installer.resume(job_id, client)}
        if action == "cancel":
            return {"job": await installer.cancel(job_id)}
    except mi.InstallError as exc:
        raise _refusal(exc) from exc
    raise HTTPException(status_code=404, detail="pause, resume or cancel")


@router.delete("/jobs/{job_id}")
async def forget(request: Request, job_id: str, client: Annotated[Client, Depends(require_app)]) -> dict:
    try:
        await _installer(request).forget(job_id)
    except mi.InstallError as exc:
        raise _refusal(exc) from exc
    return {"forgotten": job_id}


@router.delete("/models/{model_id}")
async def remove(request: Request, model_id: str, client: Annotated[Client, Depends(require_app)]) -> dict[str, Any]:
    try:
        return {"job": await _installer(request).remove(model_id)}
    except (mi.InstallError, llama_config.ConfigError) as exc:
        raise _refusal(exc) from exc
