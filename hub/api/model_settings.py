"""/v1/model_settings - what the user set for each model (D39, stage 8; hub/model_settings.py), and /v1/models/check -
a short answer from a model with its settings, to see that it works and how fast.

Reading is for every client (an agent may want to know its model's parameters); changing and checking are the desktop
app's alone: the settings decide what every model says and costs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import budgets, model_settings, store
from ..auth import APP_CLIENT, Client
from ..db import utc_now
from ..gateway import GatewayError
from ..gateway.base import cost_usd
from ..limits import LIMITS
from .deps import require_client

router = APIRouter(prefix="/v1")
CHECK_PROMPT = "Ответь одним коротким предложением: ты на связи?"


def require_app(client: Annotated[Client, Depends(require_client)]) -> Client:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app changes the models' settings")
    return client


async def _known(request: Request, model_id: str) -> dict[str, Any] | None:
    """The catalog row; None for the general row. An unknown model is a 404."""
    if model_id == model_settings.ALL:
        return None
    model = await request.app.state.db.acall(lambda conn: store.get_model(conn, model_id))
    if model is None:
        raise HTTPException(status_code=404, detail=f"no model {model_id!r} in the catalog")
    return model


def _described(request: Request, model_id: str, model: dict | None, number: int, settings: dict, past: list) -> dict:
    accepts = sorted(request.app.state.gateway.accepts(model_id)) if model is not None else []
    return {"model_id": model_id, "model": model, "version": number, "settings": settings, "history": past,
            "accepts": accepts, "params": list(model_settings.PARAMS), "presets": model_settings.PRESETS,
            "efforts": list(model_settings.EFFORTS), "roles": list(model_settings.ROLES),
            "limits": {k: {"default": v[0], "low": v[1], "high": v[2]} for k, v in LIMITS.items()}}  # fmt: skip


@router.get("/model_settings")
async def read(request: Request, client: Annotated[Client, Depends(require_client)], model_id: str = "") -> dict:
    model = await _known(request, model_id)

    def gather(conn):
        number, settings = model_settings.current(conn, model_id)
        return number, settings, model_settings.history(conn, model_id)

    number, settings, past = await request.app.state.db.acall(gather)
    return _described(request, model_id, model, number, settings, past)


class Saved(BaseModel):
    model_id: str = ""
    settings: dict[str, Any]


@router.put("/model_settings")
async def write(request: Request, body: Saved, client: Annotated[Client, Depends(require_app)]) -> dict:
    await _known(request, body.model_id)
    settings = body.settings
    if not body.model_id:  # the general row: the system prompt for every model, and the roles
        settings = {"system_prompt": settings.get("system_prompt"), "roles": settings.get("roles")}
        for role, model_id in (settings["roles"] or {}).items():
            if model_id and await request.app.state.db.acall(lambda conn, m=model_id: store.get_model(conn, m)) is None:
                raise HTTPException(status_code=422, detail=f"роль «{role}»: нет модели {model_id!r}")
    return await _save(request, body.model_id, settings, client)


class Revert(BaseModel):
    model_id: str = ""
    version: int


@router.post("/model_settings/revert")
async def revert(request: Request, body: Revert, client: Annotated[Client, Depends(require_app)]) -> dict:
    """An earlier version back - as a new version, so the one replaced stays too."""
    await _known(request, body.model_id)
    old = await request.app.state.db.acall(lambda conn: model_settings.version(conn, body.model_id, body.version))
    if old is None:
        raise HTTPException(status_code=404, detail=f"no version {body.version} for {body.model_id!r}")
    return await _save(request, body.model_id, old, client)


async def _save(request: Request, model_id: str, settings: dict[str, Any], client: Client) -> dict:
    db = request.app.state.db

    def save(conn):
        number = model_settings.save(conn, model_id, settings, by=client.name)
        model = store.get_model(conn, model_id) if model_id else None
        return number, model, model_settings.current(conn, model_id)[1], model_settings.history(conn, model_id)

    try:
        number, model, now, past = await db.acall(save)
    except model_settings.SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.bus.publish(
        "model.settings", {"model_id": model_id, "version": number, "name": model["name"] if model else ""}
    )
    return _described(request, model_id, model, number, now, past)


class Check(BaseModel):
    model_id: str


@router.post("/models/check")
async def check(request: Request, body: Check, client: Annotated[Client, Depends(require_app)]) -> dict:
    """One short answer with the model's settings and system prompts: its words, how soon the first one came, how
    fast the rest, what it cost (recorded as usage of the app, so budgets count it)."""
    model = await _known(request, body.model_id)
    if model is None or not model["enabled"]:
        raise HTTPException(status_code=404, detail="this model is not offered now")
    return await run_check(request.app.state, model, client)


async def run_check(state: Any, model: dict[str, Any], client: Client) -> dict:
    """The check itself - also the trial of a local model's new launch settings (hub/api/llama.py)."""
    if model["prices"] and await state.db.acall(lambda conn: budgets.reached(conn, model["id"])):
        raise HTTPException(status_code=402, detail="a budget for this model is used up")
    number, own = await state.db.acall(lambda conn: model_settings.current(conn, model["id"]))
    general = (await state.db.acall(lambda conn: model_settings.current(conn, model_settings.ALL)))[1]
    system = "\n\n".join(p for p in (general.get("system_prompt"), own.get("system_prompt")) if p)
    params = model_settings.clean_params(own.get("params") or {}, state.gateway.accepts(model["id"]))
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content":
                                                                                  CHECK_PROMPT}]  # fmt: skip
    try:
        provider, ref = state.gateway.resolve(model["id"])
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    started = time.monotonic()
    text: list[str] = []
    end = None
    extra = {"params": params} if params else {}
    try:
        async with asyncio.timeout(300):  # a local model may load for minutes, no longer
            async for event in provider.stream_chat(ref, messages, own.get("effort") or "low", asyncio.Event(),
                                                    **extra):  # fmt: skip
                if event.kind == "content":
                    text.append(event.text)
                elif event.kind == "end":
                    end = event
    except (GatewayError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc) or "no answer in 5 minutes", "seconds": round(time.monotonic() - started,
                                                                                            1)}  # fmt: skip
    usage = (end.usage if end else None) or {}
    cost = cost_usd(usage, model["prices"])

    def record(conn):
        store.record_usage(conn, client_id=client.id, conversation_id=None, message_id=None, model_id=model["id"],
                           usage=usage, cost_usd=cost, estimated=bool(usage.get("estimated")),
                           meta={"check": True, "stats": (end.stats if end else None) or {},
                                 **({"params": params} if params else {}),
                                 **({"settings_version": number} if number else {})})  # fmt: skip
        return budgets.crossed(conn, model["id"], cost)

    for kind, budget in await state.db.acall(record):
        await state.bus.publish(f"budget.{kind}", {"model_id": model["id"], "budget": budget})
    stats = (end.stats if end else None) or {}
    return {"ok": not (end and end.refusal), "text": "".join(text).strip() or (end.refusal if end else "") or "",
            "seconds": round(time.monotonic() - started, 1), "ttft_s": stats.get("ttft_s"),
            "tok_s": stats.get("tok_s"), "output_tokens": usage.get("output_tokens", 0), "cost_usd": cost,
            "params": params}  # fmt: skip


EXPORT_KIND = "localchat-model-settings"


@router.get("/model_settings/export")
async def export(request: Request, client: Annotated[Client, Depends(require_app)]) -> dict:
    """Every model's settings as they are now, and the general row - a file to keep or to take to another hub."""
    current = await request.app.state.db.acall(model_settings.exported)
    return {"kind": EXPORT_KIND, "version": 1, "exported_at": utc_now(), **current}


class Imported(BaseModel):
    data: dict[str, Any]


@router.post("/model_settings/import")
async def import_settings(request: Request, body: Imported, client: Annotated[Client, Depends(require_app)]) -> dict:
    """An exported file back: each model of this hub gets its settings as a new version; a model this hub does not
    have is skipped, a role naming one is dropped; a wrong value is said, and the rest still saved."""
    data = body.data
    if data.get("kind") != EXPORT_KIND:
        raise HTTPException(status_code=422, detail="это не файл настроек моделей AFO")
    db = request.app.state.db

    def apply(conn):
        known = {m["id"] for m in store.list_models(conn, enabled_only=False)}
        report: dict[str, Any] = {"saved": [], "skipped": [], "errors": {}}
        general = dict(data.get("general") or {})
        roles = general.get("roles") or {}
        report["skipped"] += [f"роль {role}: {mid}" for role, mid in roles.items() if mid not in known]
        general["roles"] = {role: mid for role, mid in roles.items() if mid in known}
        entries = [(model_settings.ALL, {k: general.get(k) for k in ("system_prompt", "roles")})]
        entries += list((data.get("models") or {}).items())
        for model_id, settings in entries:
            if model_id and model_id not in known:
                report["skipped"].append(model_id)
                continue
            try:
                model_settings.save(conn, model_id, settings, by=f"{client.name} (импорт)")
            except model_settings.SettingsError as exc:
                report["errors"][model_id or "общие"] = str(exc)
                continue
            report["saved"].append(model_id or "общие")
        return report

    report = await db.acall(apply)
    await request.app.state.bus.publish("model.settings", {"model_id": "*", "imported": len(report["saved"])})
    return report
