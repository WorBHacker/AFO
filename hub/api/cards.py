"""/v1/projects/{id}/card - the project card (stage 3, D24): a model drafts it, the user confirms it.

Reading is open to every client; drafting, confirming, restoring and dropping a draft are the app's. A draft is
written in the background: `card.drafting` (live) when it starts, `card.drafted` or `card.failed` when it ends;
`card.confirmed` when the user confirms a version. A cloud model may not draft the card of a local-only project
(D10). A paid model's draft is checked against budgets first and its cost recorded like any request.

A confirmed version keeps what the project was like then; the card's state says what changed since (`changes`), and
a draft with `update` brings the confirmed card up to date instead of writing it anew (D24).

The confirmed card goes into the project only on the user's button (D23): `GET .../card/export` says what writing
CLAUDE.md, AGENTS.md or .clinerules would do to each file, `POST .../card/export` writes the chosen ones
(hub/export.py) - the app's only; `card.exported` goes into the feed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import budgets, cards, export, model_settings, notes, projects, store
from ..auth import APP_CLIENT, Client
from ..cards import CardError
from ..export import ExportError
from ..gateway import GatewayError
from ..gateway.base import cost_usd
from ..projects import ProjectError
from .deps import require_client

log = logging.getLogger("hub.cards")
router = APIRouter(prefix="/v1")
DRAFT_EFFORT = "medium"


class DraftRequest(BaseModel):
    model_id: str
    update: bool = False  # bring the confirmed card up to date, touching only what changed


class CardText(BaseModel):
    text: str = Field(min_length=1)


class Version(BaseModel):
    version: int


class ExportRequest(BaseModel):
    targets: list[str] = Field(min_length=1)  # claude, agents, cline


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change project cards")


async def _project(request: Request, project_id: str) -> projects.Project:
    try:
        return await request.app.state.db.acall(lambda conn: projects.get_project(conn, project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _drafting(request: Request) -> set[str]:
    state = request.app.state
    if not hasattr(state, "card_drafts"):
        state.card_drafts = set()
    return state.card_drafts


async def _state(request: Request, project: projects.Project) -> dict:
    state = await request.app.state.db.acall(lambda conn: cards.card_state(conn, project.id))
    confirmed = state["confirmed"]
    missing = await asyncio.to_thread(cards.missing_paths, project, confirmed["text"]) if confirmed else []
    changes = None
    if confirmed:
        baseline = await request.app.state.db.acall(lambda conn: cards.baseline(conn, project.id))
        changes = await asyncio.to_thread(cards.changes_since, project, baseline)
    return {**state, "missing": missing, "changes": changes, "drafting": project.id in _drafting(request)}


@router.get("/projects/{project_id}/card")
async def get_card(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await _state(request, await _project(request, project_id))


async def _export_plans(request: Request, project: projects.Project, targets: list[str]) -> list[export.Plan]:
    card, written = await request.app.state.db.acall(
        lambda conn: (cards.confirmed_text(conn, project.id), notes.list_notes(conn, project.id))
    )
    if not card:
        raise HTTPException(status_code=409, detail="the project has no confirmed card to export")
    text = export.block(project, card, written)
    try:
        return [await asyncio.to_thread(export.plan, project, target, text) for target in targets]
    except ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects/{project_id}/card/export")
async def export_preview(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """What writing the card into each file would do: create it, add the block at its end, replace the block."""
    _app_only(client)
    project = await _project(request, project_id)
    plans = await _export_plans(request, project, list(export.TARGETS))
    return {"targets": [p.to_json(project.root) for p in plans], "block": plans[0].after if plans else ""}


@router.post("/projects/{project_id}/card/export")
async def export_card(
    project_id: str, body: ExportRequest, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    project = await _project(request, project_id)
    plans = await _export_plans(request, project, list(dict.fromkeys(body.targets)))
    snapshots = request.app.state.settings.paths.snapshots
    files = []
    for item in plans:
        try:
            kept = await asyncio.to_thread(export.write, item, snapshots)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"{item.path.name} was not written: {exc}") from exc
        files.append({**item.to_json(project.root), "snapshot": kept})
    await request.app.state.bus.publish(
        "card.exported", {"project_id": project.id, "files": files}, project_id=project.id
    )
    return {"files": files}


@router.post("/projects/{project_id}/card/draft", status_code=202)
async def draft_card(
    project_id: str, body: DraftRequest, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    project = await _project(request, project_id)
    db = request.app.state.db
    model = await db.acall(lambda conn: store.get_model(conn, body.model_id))
    if model is None or not model["enabled"]:
        raise HTTPException(status_code=422, detail=f"no such model available: {body.model_id}")
    if model["locality"] == "cloud" and not project.cloud_ok:
        raise HTTPException(
            status_code=409, detail=f"«{project.name}» is local-only: a cloud model may not read it (D10)"
        )
    if model["prices"] and await db.acall(lambda conn: budgets.reached(conn, model["id"])):
        raise HTTPException(status_code=409, detail="a budget for this model is used up")
    if project.id in _drafting(request):
        raise HTTPException(status_code=409, detail="a draft is being written already")
    current = None
    if body.update:
        current = await db.acall(
            lambda conn: (cards.confirmed_text(conn, project.id), cards.baseline(conn, project.id))
        )
        if current[0] is None:
            raise HTTPException(status_code=409, detail="there is no confirmed card to bring up to date")
    _drafting(request).add(project.id)
    task = asyncio.create_task(_write_draft(request.app, project, model, client.id, current))
    task.add_done_callback(lambda t: t.exception())
    await request.app.state.bus.publish(
        "card.drafting", {"project_id": project.id, "model_id": model["id"]}, project_id=project.id, persist=False
    )
    return {"drafting": True}


async def _write_draft(app, project: projects.Project, model: dict, client_id: str | None, current=None) -> None:
    """`current`: (the confirmed card, its baseline) for an update; None for a card written anew."""
    bus, db = app.state.bus, app.state.db
    try:
        if current is None:
            messages = await asyncio.to_thread(cards.prompt, project)
        else:
            messages = await asyncio.to_thread(cards.update_prompt, project, *current)
        provider, model_ref = app.state.gateway.resolve(model["id"])
        # the answer's parameters the user set for this model (D39) - temperature, thinking, Strata's refusal vector
        own = (await db.acall(lambda conn: model_settings.current(conn, model["id"])))[1]
        params = model_settings.clean_params(own.get("params") or {}, app.state.gateway.accepts(model["id"]))
        extra = {"params": params} if params else {}
        text, end = [], None
        async for event in provider.stream_chat(model_ref, messages, DRAFT_EFFORT, asyncio.Event(), **extra):
            if event.kind == "content":
                text.append(event.text)
            elif event.kind == "end":
                end = event
        usage = (end.usage if end else None) or {}
        cost = cost_usd(usage, model["prices"])

        def write(conn):
            meta = {"purpose": "card", "project_id": project.id}
            store.record_usage(conn, client_id=client_id, conversation_id=None, message_id=None, model_id=model["id"],
                               usage=usage, cost_usd=cost, meta=meta)  # fmt: skip
            if end is not None and end.refusal:
                return None
            # an update: the sections the model rewrote, merged into the confirmed card
            draft = "".join(text).strip() if current is None else cards.merge_update(current[0], "".join(text))
            if draft:
                cards.save_draft(conn, project.id, draft, model["id"])
            return draft

        draft = await db.acall(write)
        if not draft:
            if end is not None and end.refusal:
                reason = end.refusal
            elif current is not None and "".join(text).strip():
                reason = tr("модель не нашла, что менять в карточке")
            else:
                reason = "the model wrote nothing"
            raise GatewayError(reason)
        await bus.publish("card.drafted", {"project_id": project.id, "name": project.name, "model_id": model["id"],
                                           "cost_usd": cost, "update": current is not None},
                          project_id=project.id)  # fmt: skip
    except (GatewayError, CardError) as exc:
        await bus.publish("card.failed", {"project_id": project.id, "detail": str(exc)}, project_id=project.id)
    except Exception:
        log.exception("drafting the card of %s failed", project.id)
        await bus.publish("card.failed", {"project_id": project.id, "detail": "internal error"}, project_id=project.id)
    finally:
        app.state.card_drafts.discard(project.id)


@router.put("/projects/{project_id}/card")
async def confirm_card(
    project_id: str, body: CardText, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    """The user's text (the draft as edited, or their own) becomes the next confirmed version."""
    _app_only(client)
    project = await _project(request, project_id)
    now = await asyncio.to_thread(cards.snapshot, project)  # what this version is confirmed against
    try:
        state = await request.app.state.db.acall(lambda conn: cards.confirm(conn, project.id, body.text, now))
    except CardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    version = state["confirmed"]["version"]
    await request.app.state.bus.publish(
        "card.confirmed", {"project_id": project.id, "name": project.name, "version": version}, project_id=project.id
    )
    return await _state(request, project)


@router.post("/projects/{project_id}/card/restore")
async def restore_card(
    project_id: str, body: Version, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    project = await _project(request, project_id)
    now = await asyncio.to_thread(cards.snapshot, project)
    try:
        state = await request.app.state.db.acall(lambda conn: cards.restore(conn, project.id, body.version, now))
    except CardError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await request.app.state.bus.publish(
        "card.confirmed",
        {"project_id": project.id, "name": project.name, "version": state["confirmed"]["version"],
         "restored_from": body.version},
        project_id=project.id,
    )  # fmt: skip
    return await _state(request, project)


@router.delete("/projects/{project_id}/card/draft", status_code=204)
async def drop_draft(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    project = await _project(request, project_id)
    if not await request.app.state.db.acall(lambda conn: cards.drop_draft(conn, project.id)):
        raise HTTPException(status_code=404, detail="there is no draft")
    await request.app.state.bus.publish("card.dropped", {"project_id": project.id}, project_id=project.id,
                                        persist=False)  # fmt: skip
