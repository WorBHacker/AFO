"""/v1/projects/{id}/notes and /v1/notes/{id} - a project's notes for models (stage 3, D22 L4).

Reading is open to every client; writing is the app's. `note.changed` (live) tells clients to read them again.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import notes, projects
from ..auth import APP_CLIENT, Client
from ..notes import NoteError
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1")


class NoteText(BaseModel):
    text: str


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may write notes")


async def _changed(request: Request, project_id: str) -> None:
    await request.app.state.bus.publish("note.changed", {"project_id": project_id}, project_id=project_id,
                                        persist=False)  # fmt: skip


@router.get("/projects/{project_id}/notes")
async def list_notes(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"notes": await request.app.state.db.acall(lambda conn: notes.list_notes(conn, project_id))}


@router.post("/projects/{project_id}/notes", status_code=201)
async def add_note(
    project_id: str, body: NoteText, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)

    def write(conn):
        projects.get_project(conn, project_id)
        return notes.add_note(conn, project_id, body.text)

    try:
        note = await request.app.state.db.acall(write)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _changed(request, project_id)
    return note


@router.put("/notes/{note_id}")
async def update_note(
    note_id: int, body: NoteText, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    try:
        note = await request.app.state.db.acall(lambda conn: notes.update_note(conn, note_id, body.text))
    except NoteError as exc:
        raise HTTPException(status_code=404 if "no such" in str(exc) else 422, detail=str(exc)) from exc
    await _changed(request, note["project_id"])
    return note


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: int, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    project_id = await request.app.state.db.acall(lambda conn: notes.delete_note(conn, note_id))
    if project_id is None:
        raise HTTPException(status_code=404, detail="no such note")
    await _changed(request, project_id)
