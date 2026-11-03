"""/v1/settings - all of the hub's settings as one file, and the file back after a preview (D46, step 13.10;
hub/portable.py). The desktop app's alone: the file holds what every model is told, the projects and what agents may
do there. The parts behind the connections' password load with its ticket (X-LocalChat-Unlock), as their own
changes do.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import autostart, portable
from ..auth import Client
from ..projects import get_project
from .connections import Ticket, _app_only
from .deps import require_client

router = APIRouter(prefix="/v1/settings")


class Loaded(BaseModel):
    data: dict[str, Any]
    parts: list[str] | None = Field(default=None, max_length=20)  # the parts to load; None - all the file holds


@router.get("/export")
async def export(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Every setting as it is now, no secret among them: a file to keep or to take to another hub."""
    _app_only(client)
    return await request.app.state.db.acall(portable.export)


@router.post("/import/preview")
async def preview(body: Loaded, request: Request, client: Annotated[Client, Depends(require_client)],
                  ticket: Ticket = None) -> dict:  # fmt: skip
    """What loading the file would do, line by line - loaded and taken back, nothing written."""
    return await _load(request, body, client, ticket, apply=False)


@router.post("/import")
async def import_settings(body: Loaded, request: Request, client: Annotated[Client, Depends(require_client)],
                          ticket: Ticket = None) -> dict:  # fmt: skip
    """The file loaded: it adds and changes, never removes; an MCP server and a schedule come in turned off."""
    state = request.app.state
    result = await _load(request, body, client, ticket, apply=True)
    for project_id in result.pop("added_projects"):
        project = await state.db.acall(lambda conn, p=project_id: get_project(conn, p))
        state.watcher.watch(project)
        await state.bus.publish("project.added", {"project": project.to_json()}, project_id=project.id)
    state.processes.soon()
    await state.db.acall(autostart.load_choices)
    if result.pop("catalog"):
        await state.bus.publish("models.synced", await state.gateway.sync_catalog())
    counts = {a: sum(1 for p in result["parts"] for i in p["items"] if i["action"] == a) for a in ("add", "change")}
    await state.bus.publish("settings.imported", {"parts": [p["id"] for p in result["parts"]], **counts})
    return result


async def _load(request: Request, body: Loaded, client: Client, ticket: str | None, *, apply: bool) -> dict:
    _app_only(client)
    state = request.app.state
    unlocked = state.lock.is_open(ticket)
    chosen = set(body.parts) if body.parts is not None else None
    by = f"{client.name} (импорт)"
    try:
        result = await state.db.acall(
            lambda conn: portable.load(conn, body.data, apply=apply, unlocked=unlocked, chosen=chosen, by=by)
        )
    except portable.PortableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not apply:
        del result["added_projects"], result["catalog"]
    return result
