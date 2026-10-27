"""/v1/.../changes and /v1/.../rules - what the hub's models wrote, and the project's standing answers (stage 5, D17).

A change is one write a model made (hub/changes.py): reading the list and a change's diff is open to every client;
undoing a change is the app's. A rule is an answer "always for this project" given in the approval queue; the app
lists them and may remove one. `file.reverted` goes into the feed.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import approvals, changes, projects
from ..auth import APP_CLIENT, Client
from ..changes import WriteError
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1")


class Undo(BaseModel):
    force: bool = False  # undo even if the file has changed since (those changes are lost)


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may do this")


async def _change(request: Request, change_id: int) -> tuple[dict, projects.Project]:
    def read(conn):
        change = changes.get_change(conn, change_id)
        return change, projects.get_project(conn, change["project_id"])

    try:
        return await request.app.state.db.acall(read)
    except (WriteError, ProjectError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/changes")
async def list_changes(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], limit: int = 50
) -> dict:
    found = await request.app.state.db.acall(
        lambda conn: changes.list_changes(conn, project_id, max(1, min(limit, 500)))
    )
    return {"changes": found}


@router.get("/changes/{change_id}")
async def get_change(change_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The change with its diff, from the versions the hub kept."""
    change, _project = await _change(request, change_id)
    folder = request.app.state.settings.paths.snapshots

    def build() -> str:
        before = changes.load(folder, change["before_hash"]) if change["before_hash"] else None
        return changes.diff(change["path"], before, changes.load(folder, change["after_hash"]))

    try:
        return {**change, "diff": await asyncio.to_thread(build)}
    except WriteError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc


@router.post("/changes/{change_id}/revert")
async def revert(
    change_id: int, body: Undo, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    change, project = await _change(request, change_id)
    try:
        await asyncio.to_thread(changes.revert, change, project, request.app.state.settings.paths.snapshots,
                                force=body.force)  # fmt: skip
    except WriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    done = await request.app.state.db.acall(lambda conn: changes.mark_reverted(conn, change_id))
    await request.app.state.bus.publish("file.reverted", {"change": done, "project_id": project.id},
                                        project_id=project.id)  # fmt: skip
    return done


@router.get("/projects/{project_id}/rules")
async def list_rules(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"rules": await request.app.state.db.acall(lambda conn: approvals.list_rules(conn, project_id))}


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    removed = await request.app.state.db.acall(lambda conn: approvals.delete_rule(conn, rule_id))
    if removed is None:
        raise HTTPException(status_code=404, detail=f"no such rule: {rule_id}")
    await request.app.state.bus.publish("rules.changed", {"project_id": removed["project_id"]},
                                        project_id=removed["project_id"], persist=False)  # fmt: skip
