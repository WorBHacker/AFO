"""/v1/editor - what the LocalChat extension for VS Code reports (stage 6.6): the open file, finished terminal commands.
Any client with a token may report (the extension has its own, `python -m hub token create vscode`); everyone reads.
And the window's own editor (D47): which files it holds with edits not saved yet - the window's alone."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import projects
from ..auth import Client
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1/editor")


class Focus(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    line: int = Field(default=1, ge=1)


class Command(BaseModel):
    terminal: str = Field(default="", max_length=200)
    cwd: str = Field(default="", max_length=1000)
    command: str = Field(min_length=1, max_length=20_000)
    exit_code: int | None = None
    output: str = Field(default="", max_length=200_000)
    duration_s: float | None = Field(default=None, ge=0)


class Unsaved(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)  # inside the project, as GET .../file names it


class Dirty(BaseModel):
    files: list[Unsaved] = Field(default_factory=list, max_length=100)


@router.post("/focus")
async def focus(body: Focus, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await request.app.state.editor.focus(client.name, body.path, body.line)


@router.post("/command")
async def command(body: Command, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await request.app.state.editor.command(client.name, **body.model_dump())


@router.put("/dirty")
async def dirty(body: Dirty, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The files the window's editor holds with edits not saved yet (D47, step 14.2c): the whole list, each time it
    changes and whenever the window's events connect; the hub forgets it a while after the last window went. A
    model's write into one of them is asked in every mode, a trusted chat's too, and no answer covers it."""
    found = []
    for f in body.files:
        try:
            project = await request.app.state.db.acall(lambda conn, pid=f.project_id: projects.get_project(conn, pid))
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            found.append(projects.resolve_inside(project.root, f.path))
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.approvals.unsaved.say(found)
    return {"files": [f.model_dump() for f in body.files]}


@router.get("")
async def state(request: Request, client: Annotated[Client, Depends(require_client)], limit: int = 20) -> dict:
    """The open file and the latest commands, newest first."""

    def latest(conn) -> list[dict]:
        rows = conn.execute(
            "SELECT ts, project_id, payload FROM events WHERE type = 'editor.command' ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
        return [{"ts": r["ts"], "project_id": r["project_id"], **json.loads(r["payload"])} for r in rows]

    return {"focus": request.app.state.editor.shown(), "commands": await request.app.state.db.acall(latest)}
