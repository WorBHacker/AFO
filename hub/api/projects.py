"""/v1/projects - the registry, a project's tree, files and git (stage 2, D4).

Reading (tree, file, git) is open to every client with a token - to one other than the window as MCP has it (D32,
D46): a cloud client sees only the projects that allow the cloud, a secret file is not given, text goes through the
secret filter. Adding, changing and removing projects is the app's. File and git work runs on worker threads, so a
slow `git` never holds up the hub. A project is watched from the moment it is added (hub/watch.py) until it is
removed.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import projects, search, symbols
from ..auth import APP_CLIENT, Client
from ..projects import ProjectError
from ..secret_filter import clean, is_secret_file
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


def _may_see(client: Client, project: projects.Project) -> bool:
    """The window sees every project; a cloud client only those that allow the cloud (D10, D32)."""
    return client.name == APP_CLIENT or not client.cloud or project.cloud_ok


async def _project(request: Request, project_id: str, client: Client | None = None) -> projects.Project:
    try:
        found = await request.app.state.db.acall(lambda conn: projects.get_project(conn, project_id))
    except ProjectError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if client is not None and not _may_see(client, found):
        raise HTTPException(status_code=404, detail=f"no project {project_id}")
    return found


async def _off_loop(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects")
async def list_projects(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    found = await request.app.state.db.acall(projects.list_projects)
    return {"projects": [p.to_json() for p in found if _may_see(client, p)]}


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
    project = await _project(request, project_id, client)
    return {"path": path, "entries": await _off_loop(projects.tree, project.root, path)}


@router.get("/projects/{project_id}/file")
async def project_file(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], path: str
) -> dict:
    project = await _project(request, project_id, client)
    if client.name != APP_CLIENT and projects.secret_file(project.root, path):
        raise HTTPException(status_code=403, detail=f"{path} holds secrets: only the window shows it")
    found = await _off_loop(projects.read_file, project.root, path)
    return found if client.name == APP_CLIENT else {**found, "text": clean(found["text"])}


@router.get("/projects/{project_id}/git")
async def project_git(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], log: int = 20
) -> dict:
    project = await _project(request, project_id, client)
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
    project = await _project(request, project_id, client)
    found = await _off_loop(search.search, project.root, q, regex=regex, case=case, glob=glob, limit=limit)
    if client.name != APP_CLIENT:  # as a model's search: no secret file, no secret in a line
        found["files"] = [{**f, "matches": [{**m, "text": clean(m["text"])} for m in f["matches"]]}
                          for f in found["files"] if not is_secret_file(f["path"])]  # fmt: skip
    return found


@router.get("/projects/{project_id}/files")
async def project_files(
    project_id: str, request: Request, client: Annotated[Client, Depends(require_client)], q: str = "", limit: int = 20
) -> dict:
    """Files whose path matches `q`, best first - for picking a file by a few letters (hub/search.py)."""
    project = await _project(request, project_id, client)
    return {"files": await _off_loop(search.find_files, project.root, q, max(1, min(limit, 200)))}


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
    project = await _project(request, project_id, client)
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
    project = await _project(request, project_id, client)
    if client.name != APP_CLIENT and path and projects.secret_file(project.root, path):
        raise HTTPException(status_code=403, detail=f"{path} holds secrets: only the window shows its changes")
    found = await _off_loop(projects.git_diff, project.root, path, staged=staged)
    if client.name != APP_CLIENT:  # a secret file's changes left out whole, the rest through the filter
        sections = re.split(r"(?=^diff --git )", found["diff"], flags=re.M)
        kept = [d for d in sections
                if not (m := re.match(r"diff --git a/(.+?) b/", d)) or not is_secret_file(m.group(1))]  # fmt: skip
        found["diff"] = clean("".join(kept))
    return found
