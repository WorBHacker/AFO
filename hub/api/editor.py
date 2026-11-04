"""/v1/editor - what the LocalChat extension for VS Code reports (stage 6.6): the open file, finished terminal commands.
Any client with a token may report (the extension has its own, `python -m hub token create vscode`); everyone reads.
And the window's own editor (D47): which files it holds with edits not saved yet, and its code completion - the
window's alone."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import projects
from ..auth import Client
from ..completion import AFTER, BEFORE, Paused
from ..projects import ProjectError
from ..rewrite import Refused, rewrite
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


class Around(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)
    before: str = Field(default="", max_length=BEFORE * 4)  # the code before the cursor (the hub keeps BEFORE of it)
    after: str = Field(default="", max_length=AFTER * 4)
    load: bool = False  # the person asked: a local model not loaded may be loaded now


class Edit(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)
    selected: str = Field(max_length=200_000)
    before: str = Field(default="", max_length=20_000)
    after: str = Field(default="", max_length=20_000)
    instruction: str = Field(min_length=1, max_length=4000)


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


@router.post("/complete")
async def complete(body: Around, request: Request, client: Annotated[Client, Depends(require_client)]):
    """Code completion at the cursor (D47, step 14.6), the window's alone: lines of JSON - {model, how}, then
    {text} pieces, then {done}; or one {paused, message} when it does not run now (no model chosen, a local one not
    loaded, a cloud one for a local-only project, a secret file). The next request cancels this one."""
    try:
        project = await request.app.state.db.acall(lambda conn: projects.get_project(conn, body.project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        head, pieces = await request.app.state.completions.start(project, body.path, body.before, body.after,
                                                                 load=body.load)  # fmt: skip
    except Paused as exc:
        head, pieces = {"paused": exc.reason, "message": str(exc)}, None

    async def lines():
        yield json.dumps(head, ensure_ascii=False) + "\n"
        if pieces is not None:
            async for piece in pieces:
                yield json.dumps({"text": piece}, ensure_ascii=False) + "\n"
            yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/edit")
async def edit(body: Edit, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The model edits the selection (D47, step 14.7, Ctrl+I), the window's alone: {replacement, model} - the window
    shows it as a diff, the person takes it or not. 409 {reason, message} when it may not be done (no model chosen,
    a cloud one for a local-only project, a secret file)."""
    state = request.app.state
    try:
        project = await state.db.acall(lambda conn: projects.get_project(conn, body.project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return await rewrite(state.db, state.gateway, project, body.path, body.selected, body.before, body.after,
                             body.instruction)  # fmt: skip
    except Refused as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "message": str(exc)}) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=tr("модель не ответила за 2 минуты")) from exc


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
