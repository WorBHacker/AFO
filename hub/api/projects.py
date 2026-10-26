"""/v1/projects - the registry, a project's tree, files and git (stage 2, D4).

Reading (tree, file, git) is open to every client with a token; adding, changing and removing projects is the
app's. File and git work runs on worker threads, so a slow `git` never holds up the hub. A project is watched from
the moment it is added (hub/watch.py) until it is removed.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import projects, search, symbols
from ..auth import APP_CLIENT, Client
from ..projects import ProjectError
from .deps import require_client

router = APIRouter(prefix="/v1")


class NewProject(BaseModel):
    path: str
    name: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    cloud_ok: bool | None = None


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may change the project list")


async def _project(request: Request, project_id: str) -> projects.Project:
    try:
        return await request.app.state.db.acall(lambda conn: projects.get_project(conn, project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _off_loop(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects")
async def list_projects(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    found = await request.app.state.db.acall(projects.list_projects)
    return {"projects": [p.to_json() for p in found]}


@router.post("/projects", status_code=201)
async def add_project(body: NewProject, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    try:
        project = await request.app.state.db.acall(lambda conn: projects.add_project(conn, body.path, body.name))
    except ProjectError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.watcher.watch(project)
    request.app.state.processes.soon()
    await request.app.state.bus.publish("project.added", {"project": project.to_json()}, project_id=project.id)
    return project.to_json()


@router.patch("/projects/{project_id}")
async def change_project(
    project_id: str, body: ProjectPatch, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    await _project(request, project_id)
    project = await request.app.state.db.acall(
        lambda conn: projects.update_project(
            conn, project_id, name=body.name, description=body.description, cloud_ok=body.cloud_ok
        )
    )
    await request.app.state.bus.publish("project.changed", {"project": project.to_json()}, project_id=project_id)
    return project.to_json()


@router.delete("/projects/{project_id}", status_code=204)
async def remove_project(project_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    """Forgets the project; its folder is not touched."""
    _app_only(client)
    name = (await _project(request, project_id)).name  # the log keeps it: the row is about to go
    if not await request.app.state.db.acall(lambda conn: projects.remove_project(conn, project_id)):
        raise HTTPException(status_code=404, detail="no such project")
    request.app.state.watcher.unwatch(project_id)
    request.app.state.symbols.forget(project_id)
    request.app.state.processes.soon()
    await request.app.state.bus.publish("project.removed", {"id": project_id, "name": name}, project_id=project_id)


@router.get("/projects/{project_id}/tree")
async def project_tree(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str = ""
) -> dict:
    project = await _project(request, project_id)
    return {"path": path, "entries": await _off_loop(projects.tree, project.root, path)}


@router.get("/projects/{project_id}/file")
async def project_file(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str
) -> dict:
    project = await _project(request, project_id)
    return await _off_loop(projects.read_file, project.root, path)


@router.get("/projects/{project_id}/git")
async def project_git(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], log: int = 20
) -> dict:
    project = await _project(request, project_id)
    status = await _off_loop(projects.git_status, project.root)
    commits = await _off_loop(projects.git_log, project.root, max(0, min(log, 200)))
    return {**status, "log": commits}


@router.get("/projects/{project_id}/search")
async def project_search(
    project_id: str,
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    q: str,
    regex: bool = False,
    case: str = "smart",
    glob: str = "",
    limit: int = search.MAX_RESULTS,
) -> dict:
    """Lines that match `q` (text, or a regular expression with `regex`), grouped by file (hub/search.py)."""
    project = await _project(request, project_id)
    return await _off_loop(search.search, project.root, q, regex=regex, case=case, glob=glob, limit=limit)


@router.get("/projects/{project_id}/symbols")
async def project_symbols(
    project_id: str,
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    q: str = "",
    path: str = "",
    kind: str = "",
    limit: int = symbols.FIND_LIMIT,
) -> dict:
    """Definitions in the project's code (hub/symbols.py, D26): those named like `q`, or the outline of `path`."""
    project = await _project(request, project_id)
    index = request.app.state.symbols
    if path:
        outline = await _off_loop(index.outline, project, path)
        if outline is None:
            raise HTTPException(status_code=400, detail="not a code file the symbol index reads")
        return {"path": path, "symbols": outline}
    if not q.strip():
        raise HTTPException(status_code=422, detail="give q (a name) or path (a file)")
    return await _off_loop(index.find, project, q, kind=kind, limit=max(1, min(limit, 500)))


@router.get("/projects/{project_id}/diff")
async def project_diff(
    project_id: str,
    request: Request,
    client: Annotated[Client, Depends(require_client)],
    path: str = "",
    staged: bool = False,
) -> dict:
    project = await _project(request, project_id)
    return await _off_loop(projects.git_diff, project.root, path, staged=staged)
