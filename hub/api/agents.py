"""/v1/agents and /v1/runs - the hub's own agents (stage 5, D16): profiles, and runs of tasks.

Reading is open to every client; making and changing profiles, starting and stopping runs are the app's (an agent
writes files and runs commands, within its rights - D17). A run is a conversation too: its messages come from
/v1/conversations/{id}.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from common.i18n import tr

from .. import agent_tools, agents, autonomous
from ..agents import AgentError
from ..auth import APP_CLIENT, Client
from ..sandbox import SandboxError
from .connections import Ticket, _open
from .deps import require_client

router = APIRouter(prefix="/v1")


class Profile(BaseModel):
    name: str
    instructions: str = ""
    model_id: str
    tools: list[str] = Field(default_factory=list)  # [] - all of the hub's tools
    mode: str = "ask"
    project_ids: list[str] = Field(default_factory=list)
    max_steps: int = 30
    max_minutes: int = 30
    budget_usd: float | None = None


class Task(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    project_ids: list[str] | None = None  # the profile's projects when left out


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app may manage agents and their runs")


def _error(exc: AgentError) -> HTTPException:
    text = str(exc)
    return HTTPException(status_code=404 if text.startswith("no such") else 422, detail=text)


async def _db(request: Request, fn) -> Any:
    try:
        return await request.app.state.db.acall(fn)
    except AgentError as exc:
        raise _error(exc) from exc


@router.get("/agents")
async def list_profiles(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return {"agents": await _db(request, agents.list_profiles), "tools": agents.TOOL_NAMES}


@router.post("/agents", status_code=201)
async def create_profile(body: Profile, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    profile = await _db(request, lambda conn: agents.save_profile(conn, body.model_dump()))
    await request.app.state.bus.publish("agents.changed", {"agent_id": profile["id"]}, persist=False)
    return profile


@router.get("/agents/{agent_id}")
async def get_profile(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await _db(request, lambda conn: agents.get_profile(conn, agent_id))


@router.put("/agents/{agent_id}")
async def update_profile(
    agent_id: str, body: Profile, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    profile = await _db(request, lambda conn: agents.save_profile(conn, body.model_dump(), agent_id))
    await request.app.state.bus.publish("agents.changed", {"agent_id": agent_id}, persist=False)
    return profile


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_profile(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> None:
    _app_only(client)
    if not await _db(request, lambda conn: agents.delete_profile(conn, agent_id)):
        raise HTTPException(status_code=404, detail=f"no such agent: {agent_id}")
    await request.app.state.bus.publish("agents.changed", {"agent_id": agent_id}, persist=False)


@router.get("/agents/{agent_id}/versions")
async def versions(agent_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    rows = await _db(request, lambda conn: conn.execute(
        "SELECT version, data, created_at FROM agent_profile_versions WHERE profile_id = ? ORDER BY version DESC",
        (agent_id,)).fetchall())  # fmt: skip
    return {"versions": [{"version": r["version"], "created_at": r["created_at"], **json.loads(r["data"])}
                         for r in rows]}  # fmt: skip


@router.post("/agents/{agent_id}/runs", status_code=201)
async def start_run(
    agent_id: str, body: Task, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    try:
        return await request.app.state.agents.run(agent_id, body.task, body.project_ids)
    except AgentError as exc:
        raise _error(exc) from exc


@router.get("/runs")
async def list_runs(request: Request, client: Annotated[Client, Depends(require_client)], limit: int = 50) -> dict:
    count = max(1, min(limit, 500))
    return {"runs": await _db(request, lambda conn: agents.list_runs(conn, count))}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    return await _db(request, lambda conn: agents.get_run(conn, run_id))


@router.get("/runs/{run_id}/tasks")
async def run_tasks(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """A coordinator's board (16.1, D50): the tasks of its batches in the order it wrote them - the agent, what it
    waits for, the status, the run that did it and its answer."""

    def read(conn):
        agents.get_run(conn, run_id)
        return agents.list_tasks(conn, run_id)

    return {"tasks": await _db(request, read)}


async def _tasks_settled(request: Request, run_id: str) -> None:
    """The coordinator and its tasks have stopped writing: their sandboxes can be taken."""

    def read(conn):
        agents.get_run(conn, run_id)
        return agents.list_tasks(conn, run_id)

    if request.app.state.agents.busy_tasks(run_id, await _db(request, read)):
        raise HTTPException(status_code=409, detail="the tasks are still working: wait for them to end")


class TaskFiles(BaseModel):
    files: dict[str, dict[str, list[str]]] | None = None  # task_id -> project id -> the files ticked (17.1)


@router.post("/runs/{run_id}/tasks/apply")
async def apply_tasks(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)],
                      body: TaskFiles | None = None) -> dict:  # fmt: skip
    """«Применить всё» (16.2, D50): the done tasks' sandboxes onto the projects, in the order the tasks ran; the
    files that met the project's own changes at the same place carry conflict marks (`conflicts`). With `files`
    («Применить выбранные», 17.1, D51): only those, per task and project; the rest stays in the tasks' sandboxes
    (`kept`). A file a task does not have stops it all before anything is written (409)."""
    _app_only(client)
    await _tasks_settled(request, run_id)
    try:
        return await request.app.state.agents.apply_tasks(run_id, body.files if body else None)
    except SandboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/tasks/discard")
async def discard_tasks(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """«Отбросить всё» (16.2, D50): every task's sandbox goes; the projects stay as they are."""
    _app_only(client)
    await _tasks_settled(request, run_id)
    try:
        return await request.app.state.agents.discard_tasks(run_id)
    except SandboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel", status_code=202)
async def cancel_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    await _db(request, lambda conn: agents.get_run(conn, run_id))
    return {"cancelled": await request.app.state.agents.cancel(run_id)}


# --- the autonomous agent (stage 9.1, D40) ---


class Goal(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)
    done_when: str = ""
    profile_id: str | None = None  # the built-in «Автономный агент» when left out
    model_id: str | None = None  # the profile's model when left out
    planner_model_id: str | None = None  # plans first, then the run's model carries the plan out
    project_ids: list[str] | None = None
    autonomy: str = "plan"  # each_step / plan / free (Q22)
    max_steps: int = autonomous.DEFAULTS["max_steps"]
    max_minutes: int = autonomous.DEFAULTS["max_minutes"]
    max_replans: int = autonomous.DEFAULTS["max_replans"]
    budget_usd: float | None = autonomous.DEFAULTS["budget_usd"]
    network: bool = False  # its Python scripts may reach the network (9.4)
    refusal_vector: bool | None = None  # Strata's refusal vector for this task (its model must have one); None: as set


@router.post("/runs/autonomous", status_code=201)
async def start_autonomous(body: Goal, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    try:
        return await request.app.state.agents.run_autonomous(body.model_dump())
    except AgentError as exc:
        raise _error(exc) from exc


@router.get("/runs/{run_id}/plan")
async def run_plan(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """An autonomous run's goal, every version of its plan with the steps, its questions, its report."""

    def read(conn):
        info = autonomous.run_info(conn, run_id)
        if info is None:
            raise AgentError(f"no such autonomous run: {run_id}")
        return {**info, "plans": autonomous.plans(conn, run_id), "questions": autonomous.questions(conn, run_id)}

    found = await _db(request, read)
    return {**found, "paused": request.app.state.autonomy.paused(run_id)}


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


@router.get("/conversations/{conversation_id}/questions")
async def chat_questions(conversation_id: str, request: Request,
                         client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """A chat's questions still waiting for the user (its model's ask_user, Claude Code's own), with their options;
    answered through /runs/{id}/questions/{question_id} like an agent's."""
    found = await _db(request, lambda conn: autonomous.questions(conn, conversation_id))
    return {"questions": [q for q in found if q["status"] == "pending"]}


@router.post("/runs/{run_id}/questions/{question_id}")
async def answer_question(
    run_id: str, question_id: str, body: Answer, request: Request, client: Annotated[Client, Depends(require_client)]
) -> dict:
    _app_only(client)
    try:
        return await request.app.state.autonomy.answer(run_id, question_id, body.answer, by=client.name)
    except autonomous.AutonomyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _running_autonomous(request: Request, run_id: str) -> None:
    run = await _db(request, lambda conn: agents.get_run(conn, run_id))
    if run.get("kind") != "autonomous":
        raise HTTPException(status_code=422, detail="only an autonomous run can be paused or given a new plan")
    if run["status"] not in ("queued", "running"):
        raise HTTPException(status_code=409, detail=f"the run is {run['status']}")


@router.post("/runs/{run_id}/pause", status_code=202)
async def pause_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The run stops before its next model call; what it runs now finishes."""
    _app_only(client)
    await _running_autonomous(request, run_id)
    await request.app.state.autonomy.pause(run_id)
    return {"paused": True}


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    await _running_autonomous(request, run_id)
    await request.app.state.autonomy.resume(run_id)
    return {"paused": False}


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/runs/{run_id}/message")
async def message_run(run_id: str, body: Message, request: Request,
                      client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """The user writes to a running task, as in a chat: the answer to the question it waits on, or else a word it
    reads before its next step (another limit, a hint, a change of mind)."""
    _app_only(client)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail=tr("Пустое сообщение"))
    await _running_autonomous(request, run_id)
    state = request.app.state
    waiting = next((q for q in await _db(request, lambda conn: autonomous.questions(conn, run_id))
                    if q.get("status") == "pending"), None)  # fmt: skip
    if waiting is not None:
        try:
            await state.autonomy.answer(run_id, waiting["id"], text, by=client.name)
        except autonomous.AutonomyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"answered": waiting["id"]}
    state.autonomy.notify(run_id, f"The user writes to you now - take it into account from your next step: {text}")
    await state.bus.publish("agent.run", {"run_id": run_id, "message": text})
    return {"noted": True}


class Level(BaseModel):
    autonomy: str


@router.put("/runs/{run_id}/autonomy")
async def set_autonomy(run_id: str, body: Level, request: Request,
                       client: Annotated[Client, Depends(require_client)]) -> dict:  # fmt: skip
    """The run's level changes on the way; «Сам» also answers what it waits on now (its plan, a step, acting in a
    program); sending is still asked."""
    _app_only(client)
    if body.autonomy not in autonomous.AUTONOMY:
        raise HTTPException(status_code=422, detail=f"autonomy: one of {', '.join(autonomous.AUTONOMY)}")
    await _running_autonomous(request, run_id)
    state = request.app.state
    await state.db.acall(lambda conn: conn.execute("UPDATE agent_runs SET autonomy = ? WHERE conversation_id = ?",
                                                   (body.autonomy, run_id)))  # fmt: skip
    answered = []
    if body.autonomy == "free":
        for waiting in await state.approvals.pending():
            if waiting["conversation_id"] == run_id and waiting["tool"] in ("screen_act", "start_step", "set_plan"):
                await state.approvals.decide(waiting["id"], allow=True, by=client.name)
                answered.append(waiting["id"])
    await state.bus.publish("agent.run", {"run_id": run_id, "autonomy": body.autonomy})
    return {"autonomy": body.autonomy, "answered": answered}


class PlanEdit(BaseModel):
    steps: list[Any]
    reason: str = ""


@router.put("/runs/{run_id}/plan")
async def edit_plan(run_id: str, body: PlanEdit, request: Request, client: Annotated[Client, Depends(require_client)]):
    """The user's own plan for the run: a new version; the agent hears it before its next step."""
    _app_only(client)
    await _running_autonomous(request, run_id)
    try:
        return await request.app.state.autonomy.edit_plan(run_id, body.steps, body.reason, by=client.name)
    except autonomous.AutonomyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --- what the autonomous agent may reach beyond projects (stage 9.4) ---


def _access(conn) -> dict:
    return {
        "folders": agent_tools.folders(conn),
        "domains": agent_tools.domains(conn),
        "default_domains": agent_tools.DEFAULT_DOMAINS,
        "screen_cloud": agent_tools.screen_cloud(conn),
    }


@router.get("/agent/access")
async def agent_access(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    return await _db(request, _access)


class Key(BaseModel):
    host: str = Field(min_length=3, max_length=253)
    header: str = Field(default="Authorization", max_length=100)
    prefix: str = Field(default="Bearer ", max_length=50)
    value: str = Field(default="", max_length=4000)  # '' keeps the one set


@router.get("/agent/keys")
async def agent_keys(request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The user's API keys for http_request: name, host, header - and whether set; never a key."""
    _app_only(client)
    store = request.app.state.secrets
    found = await _db(request, agent_tools.keys)
    return {"keys": [{**k, "set": bool(store.get(agent_tools.KEY_SECRET + k["name"]))} for k in found]}


@router.put("/agent/keys/{name}")
async def set_agent_key(name: str, body: Key, request: Request, client: Annotated[Client, Depends(require_client)],
                        ticket: Ticket = None) -> dict:  # fmt: skip
    """A key behind the connections' password (D13): the hub adds it to the requests to its host only."""
    await _open(request, client, ticket)
    store = request.app.state.secrets

    def write(conn):
        found = [k for k in agent_tools.keys(conn) if k["name"] != name.strip().lower()]
        checked = agent_tools.check_keys([*found, {"name": name, "host": body.host, "header": body.header,
                                                   "prefix": body.prefix}])  # fmt: skip
        mine = checked[-1]
        if not body.value and not store.get(agent_tools.KEY_SECRET + mine["name"]):
            raise agent_tools.AgentToolError("the key itself")
        agent_tools.set_setting(conn, agent_tools.KEYS, checked)
        return mine

    try:
        mine = await request.app.state.db.acall(write)
    except agent_tools.AgentToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.value:
        store.set(agent_tools.KEY_SECRET + mine["name"], body.value)
    await request.app.state.bus.publish("agent.keys", {"name": mine["name"], "host": mine["host"]})
    return await agent_keys(request, client)


@router.delete("/agent/keys/{name}")
async def delete_agent_key(name: str, request: Request, client: Annotated[Client, Depends(require_client)],
                           ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)

    def write(conn):
        agent_tools.set_setting(conn, agent_tools.KEYS, [k for k in agent_tools.keys(conn) if k["name"] != name])

    await request.app.state.db.acall(write)
    request.app.state.secrets.delete(agent_tools.KEY_SECRET + name)
    await request.app.state.bus.publish("agent.keys", {"name": name, "removed": True})
    return await agent_keys(request, client)


class Access(BaseModel):
    folders: list[dict[str, Any]] | None = None  # [{path, write, cloud}]
    domains: list[str] | None = None
    screen_cloud: bool | None = None  # a cloud model may read other programs' windows (Q20, D46)


@router.put("/agent/access")
async def set_agent_access(body: Access, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)

    def write(conn):
        if body.folders is not None:
            agent_tools.set_setting(conn, agent_tools.FOLDERS, agent_tools.check_folders(body.folders))
        if body.domains is not None:
            agent_tools.set_setting(conn, agent_tools.DOMAINS, agent_tools.check_domains(body.domains))
        if body.screen_cloud is not None:
            agent_tools.set_setting(conn, agent_tools.SCREEN_CLOUD, body.screen_cloud)
        return _access(conn)

    try:
        found = await request.app.state.db.acall(write)
    except agent_tools.AgentToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.bus.publish("agent.access", {"folders": len(found["folders"])})
    return found
