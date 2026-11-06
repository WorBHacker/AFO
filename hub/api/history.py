"""/v1/.../history - a project's files version after version (stage 26, hub/history.py): what the hub kept of each
change it saw, to compare and to take back (through the save's own way, 14.1). The window's alone: other
clients read files through MCP.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import changes, history, projects
from ..auth import Client
from ..changes import WriteError
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1")


@router.get("/projects/{project_id}/history")
async def project_history(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str = "",
    limit: int = 100,
) -> dict:  # fmt: skip
    """The versions of `path`, newest first; without it - the project's files that have a history, the latest change
    first (deleted ones too)."""
    db, folder = request.app.state.db, request.app.state.settings.paths.snapshots
    if not path:
        return {"files": await db.acall(lambda conn: history.files(conn, project_id, max(1, min(limit, 500))))}
    found = await db.acall(lambda conn: history.versions(conn, folder, project_id, path, max(1, min(limit, 500))))
    return {"path": path, "versions": found}


async def _version(request: Request, version_id: int) -> tuple[dict, projects.Project]:
    def read(conn):
        version = history.get_version(conn, version_id)
        if version is None:
            raise ProjectError(f"no such version: {version_id}")
        return version, projects.get_project(conn, version["project_id"])

    try:
        return await request.app.state.db.acall(read)
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/history/{version_id}")
async def get_version(
    version_id: int, request: Request, client: Annotated[Client, Depends(require_client)],
    against: Literal["previous", "current"] = "previous",
) -> dict:  # fmt: skip
    """A version with its text and a diff: from the version before it (`previous`), or from the file as it is now to
    this version (`current`: what taking it back changes)."""
    state = request.app.state
    folder = state.settings.paths.snapshots
    version, project = await _version(request, version_id)
    before = await state.db.acall(lambda conn: history.previous(conn, version))

    def build() -> dict:
        data = None if version["deleted"] else changes.load(folder, version["hash"])
        if against == "current":
            try:
                base = projects.resolve_inside(project.root, version["path"]).read_bytes()
            except ProjectError, OSError:
                base = None
        else:
            base = None if before is None or before["deleted"] else changes.load(folder, before["hash"])
        shown = projects.decoded(data) if data is not None else {"binary": False, "text": "", "encoding": "", "eol": ""}
        return {**version, **shown, "diff": history.diff(version["path"], base, data)}

    try:
        return await asyncio.to_thread(build)
    except WriteError as exc:  # its content went over D30's size meanwhile
        raise HTTPException(status_code=410, detail=str(exc)) from exc


class Restore(BaseModel):
    base: str = ""  # sha256 of the file as the user saw it (GET .../file); "" - there is no file now


@router.post("/history/{version_id}/restore")
async def restore(
    version_id: int, body: Restore, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    """Takes the version back (stage 26.2) the way a save goes (14.1): when the file is still the version the user saw
    (`base`) - else 409 with what is there now (`reason`: changed, gone, exists) - the version's own bytes are written
    whole, and kept in «Изменения» as the user's (`restore`), to undo as any change. `file.saved` goes out."""
    state = request.app.state
    folder = state.settings.paths.snapshots
    version, project = await _version(request, version_id)
    if version["deleted"]:
        raise HTTPException(status_code=422, detail="this version is the file deleted: take back one with content")
    try:
        data = await asyncio.to_thread(changes.load, folder, version["hash"])
    except WriteError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc

    def write() -> tuple[changes.Planned, tuple[str | None, str] | None]:
        planned = changes.plan_restore(project, version["path"], data, body.base)
        if planned.before == data:
            raise WriteError(f"{planned.path} is this version already: nothing to take back")
        return planned, changes.save(planned, folder)

    try:
        planned, hashes = await asyncio.to_thread(write)
    except changes.Conflict as exc:
        try:
            now = await asyncio.to_thread(projects.read_file, project.root, version["path"])
        except ProjectError:
            now = None
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "message": str(exc), "file": now}) from exc
    except WriteError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    change = None
    if hashes is not None:
        change = await state.db.acall(lambda conn: changes.record(conn, project.id, planned, hashes,
                                                                  changes.RESTORE_TOOL, None, None))  # fmt: skip
    saved = await asyncio.to_thread(projects.read_file, project.root, planned.path)
    result = {"path": planned.path, "sha256": saved["sha256"], "size": saved["size"], "encoding": saved["encoding"],
              "eol": saved["eol"], "created": planned.before is None, "change": change,
              "restored": version_id}  # fmt: skip
    await state.bus.publish("file.saved", {"project_id": project.id, **result}, project_id=project.id)
    return result
