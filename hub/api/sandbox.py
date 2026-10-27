"""/v1/conversations/{id}/sandbox - what the model changed in a «Песочница» chat (D17, stage 9.4b): per project, the
files and the diff; «Применить» puts it onto the project's working tree, «Отбросить» throws it away. The desktop app
only: the user decides what reaches the project.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import sandbox as sb
from ..auth import Client
from ..chat import _attached
from .connections import _app_only
from .deps import require_client

router = APIRouter(prefix="/v1/conversations")


async def _projects(request: Request, conversation_id: str):
    return await request.app.state.db.acall(lambda conn: _attached(conn, conversation_id))


@router.get("/{conversation_id}/sandbox")
async def sandbox_changes(conversation_id: str, request: Request,
                          client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    _app_only(client)
    folder = request.app.state.chat.sandboxes
    found = []
    for project in await _projects(request, conversation_id):
        if sb.is_git(project):
            try:
                found.append(await asyncio.to_thread(sb.changes, folder, conversation_id, project))
            except sb.SandboxError as exc:
                found.append({"project_id": project.id, "name": project.name, "files": [], "diff": "",
                              "exists": False, "error": str(exc)})  # fmt: skip
    return {"projects": found}


async def _one(request: Request, conversation_id: str, project_id: str):
    project = next((p for p in await _projects(request, conversation_id) if p.id == project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail="no such project in this chat")
    return project


@router.post("/{conversation_id}/sandbox/{project_id}/apply")
async def apply_sandbox(conversation_id: str, project_id: str, request: Request,
                        client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    _app_only(client)
    if conversation_id in request.app.state.chat.busy:
        raise HTTPException(status_code=409, detail="the model is still writing: wait for its answer to end")
    project = await _one(request, conversation_id, project_id)
    try:
        done = await asyncio.to_thread(sb.apply, request.app.state.chat.sandboxes, conversation_id, project)
    except sb.SandboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    said = {"conversation_id": conversation_id, "project_id": project.id, **done}
    await request.app.state.bus.publish("sandbox.applied", said, project_id=project.id)
    return done


@router.post("/{conversation_id}/sandbox/{project_id}/discard")
async def discard_sandbox(conversation_id: str, project_id: str, request: Request,
                          client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    _app_only(client)
    project = await _one(request, conversation_id, project_id)
    try:
        await asyncio.to_thread(sb.discard, request.app.state.chat.sandboxes, conversation_id, project)
    except sb.SandboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    said = {"conversation_id": conversation_id, "project_id": project.id}
    await request.app.state.bus.publish("sandbox.discarded", said, project_id=project.id)
    return {"discarded": project.id}
