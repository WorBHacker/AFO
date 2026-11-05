"""The hub's own agents (stage 5, D16): profiles, and runs of tasks given to them.

A profile is who the agent is: its role (instructions), its model, which of the hub's tools it gets, its rights mode
(D17), the projects it works with, and how far a task may go - tool steps, minutes, money for a paid model (D12).
Each save is a new version, kept. A run is a task given to a profile: a conversation of kind 'agent_run' whose
first context carries the role, whose tool list is the profile's (fixed for the run, D14) and whose rights are the
profile's; it goes through the chat's loop (hub/chat.py) within the run's limits, so it asks for approvals, writes
and runs commands like a chat - and the window shows it like one.

Statuses: queued (a local model's run waits for the GPU: one at a time) -> running -> done / error / cancelled; a
running run that waits for the user's answer reads as "waiting" (its open approval says so). A run the hub was
running when it stopped goes on when the hub starts again. Events: run.started, run.finished (in the feed),
run.status (live).

A coordinator (D20) is an agent with the tool delegate_task: it gives a part of its task to another profile - a
sub-run, linked to it - waits, and gets the sub-agent's final answer as the tool's result. While it waits it gives up
the GPU (the sub-agent may need it), and takes it back after. One level only: a sub-run cannot delegate further.
Stopping the coordinator stops its sub-runs.

With delegate_tasks (16.1, D50) it gives a batch: each task a row of `delegated_tasks` - the board it and the window
read. Tasks that do not wait for each other run at once: cloud ones together (CLOUD_AT_ONCE of a batch at a time),
local ones one after another through the same GPU queue, in the batch's order grouped by model (hub/delegate.py). A
task with dependencies starts when they are done, with their answers after its prompt; one whose dependency did not
end done is skipped. The coordinator waits for the whole batch and gets the board back.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from common.i18n import tr

from . import agent_tools, autonomous, compact, computer, mail, memory, store
from .chat import CHAT_TOOLS, ChatService, Limits, Outcome
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database, utc_now
from .delegate import DELEGATE, DELEGATE_MANY, BatchError, check_batch, order
from .events import EventBus
from .gateway import GatewayError
from .projects import ProjectError, get_project

log = logging.getLogger("hub.agents")

MODES = ("read_only", "ask", "trusted")
STEPS = (1, 200)
MINUTES = (1, 24 * 60)
NAME_CHARS = 60
INSTRUCTIONS_CHARS = 8000
AGENT_TOOLS = [*CHAT_TOOLS, DELEGATE, DELEGATE_MANY]  # what a profile may choose from
TOOL_NAMES = [t["name"] for t in AGENT_TOOLS]
COORDINATING = (DELEGATE["name"], DELEGATE_MANY["name"])
DEFAULT_TOOLS = [n for n in TOOL_NAMES if n not in COORDINATING]  # "all": a coordinator is chosen, not by default
CLOUD_AT_ONCE = 4  # a batch's cloud tasks running together: the providers' request limits
ANSWER_CHARS = 6000  # of each answer on the coordinator's board
ONE_LEVEL = "you are a sub-agent yourself: you cannot give tasks further (one level, D20)"
ENDED = {"done": "done", "error": "failed", "cancelled": "cancelled"}  # a run's end -> its task's
RESUMED = "[AFO hub] The hub restarted while you worked on this task. Look at where you stopped and go on."
FIELDS = ("name", "instructions", "model_id", "tools", "mode", "project_ids", "max_steps", "max_minutes", "budget_usd")


class AgentError(Exception):
    """The message is fit to show."""


# --- profiles ---


def _profile(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) | {"tools": json.loads(row["tools"]), "project_ids": json.loads(row["project_ids"])}


def check_profile(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """The profile's fields, checked: a name, a model that exists and is not Claude Code, known tools and projects,
    limits within reason."""
    name = " ".join(str(data.get("name") or "").split())
    if not name or len(name) > NAME_CHARS:
        raise AgentError(f"an agent needs a name of 1 to {NAME_CHARS} characters")
    instructions = str(data.get("instructions") or "").strip()
    if len(instructions) > INSTRUCTIONS_CHARS:
        raise AgentError(f"instructions longer than {INSTRUCTIONS_CHARS} characters")
    model_id = str(data.get("model_id") or "")
    if model_id.startswith(f"{CLAUDE_CODE}/"):
        raise AgentError("Claude Code is an agent of its own (D16): use a chat with it, not a hub profile")
    if store.get_model(conn, model_id) is None:
        raise AgentError(f"unknown model {model_id!r}")
    tools = [str(t) for t in data.get("tools") or []]
    unknown = sorted(set(tools) - set(TOOL_NAMES))
    if unknown:
        raise AgentError(f"unknown tools: {', '.join(unknown)}; known: {', '.join(TOOL_NAMES)}")
    mode = data.get("mode") or "ask"
    if mode not in MODES:
        raise AgentError(f"unknown mode {mode!r}: {', '.join(MODES)}")
    project_ids = [str(p) for p in data.get("project_ids") or []]
    for pid in project_ids:
        try:
            get_project(conn, pid)
        except ProjectError as exc:
            raise AgentError(f"unknown project {pid!r}") from exc
    budget = data.get("budget_usd")
    if budget is not None and (not isinstance(budget, int | float) or budget <= 0):
        raise AgentError("a budget is a positive number of dollars, or none")
    return {
        "name": name, "instructions": instructions, "model_id": model_id, "tools": tools, "mode": mode,
        "project_ids": project_ids, "budget_usd": float(budget) if budget is not None else None,
        "max_steps": _within(data.get("max_steps", 30), STEPS, "max_steps"),
        "max_minutes": _within(data.get("max_minutes", 30), MINUTES, "max_minutes"),
    }  # fmt: skip


def _within(value: Any, bounds: tuple[int, int], name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not bounds[0] <= value <= bounds[1]:
        raise AgentError(f"{name} must be a whole number from {bounds[0]} to {bounds[1]}")
    return value


def save_profile(conn: sqlite3.Connection, data: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    """A new profile, or a new version of one; every version is kept."""
    fields = check_profile(conn, data)
    values = {**fields, "tools": json.dumps(fields["tools"]), "project_ids": json.dumps(fields["project_ids"])}
    try:
        if profile_id is None:
            profile_id = uuid.uuid4().hex[:12]
            conn.execute(
                f"INSERT INTO agent_profiles (id, {', '.join(FIELDS)}) VALUES (?, {', '.join('?' * len(FIELDS))})",
                (profile_id, *(values[f] for f in FIELDS)),
            )
        else:
            updated = conn.execute(
                f"UPDATE agent_profiles SET {', '.join(f'{f} = ?' for f in FIELDS)}, version = version + 1,"
                " updated_at = ? WHERE id = ?",
                (*(values[f] for f in FIELDS), utc_now(), profile_id),
            ).rowcount
            if not updated:
                raise AgentError(f"no such agent: {profile_id}")
    except sqlite3.IntegrityError as exc:
        raise AgentError(f"an agent named «{fields['name']}» exists already") from exc
    profile = get_profile(conn, profile_id)
    conn.execute(
        "INSERT INTO agent_profile_versions (profile_id, version, data) VALUES (?, ?, ?)",
        (profile_id, profile["version"], json.dumps({f: profile[f] for f in FIELDS}, ensure_ascii=False)),
    )
    return profile


def get_profile(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise AgentError(f"no such agent: {profile_id}")
    return _profile(row)


def list_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_profile(r) for r in conn.execute("SELECT * FROM agent_profiles ORDER BY name").fetchall()]


def delete_profile(conn: sqlite3.Connection, profile_id: str) -> bool:
    """The profile goes; its runs stay, with the profile's name and instructions as they were."""
    return bool(conn.execute("DELETE FROM agent_profiles WHERE id = ?", (profile_id,)).rowcount)


# --- runs ---


def _last_answer(conn: sqlite3.Connection, run_id: str) -> str:
    """The run's final answer: the text of its last assistant message."""
    for message in reversed(store.get_messages(conn, run_id)):
        if message.role == "assistant":
            text = "".join(b.get("text", "") for b in message.content if b.get("type") == "text").strip()
            if text:
                return text
    return ""


RUN_SELECT = (
    "SELECT r.*, (EXISTS (SELECT 1 FROM approvals a WHERE a.conversation_id = r.conversation_id"
    " AND a.status = 'pending') OR EXISTS (SELECT 1 FROM run_questions q WHERE q.conversation_id = r.conversation_id"
    " AND q.status = 'pending')) AS waiting, (SELECT p.profile_name FROM agent_runs p"
    " WHERE p.conversation_id = r.parent_run) AS parent_name FROM agent_runs r"
)
AUTONOMOUS_PROFILE = "Автономный агент"  # the profile an autonomous run gets when none is chosen  # i18n: keep


def _run(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    run["waiting"] = bool(run["waiting"])
    run["report"] = json.loads(run["report"]) if run.get("report") else None
    run["id"] = run["conversation_id"]
    run["shown_status"] = "waiting" if run["status"] == "running" and run["waiting"] else run["status"]
    return run


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(f"{RUN_SELECT} WHERE r.conversation_id = ?", (run_id,)).fetchone()
    if row is None:
        raise AgentError(f"no such run: {run_id}")
    return _run(row)


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(f"{RUN_SELECT} ORDER BY r.created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_run(r) for r in rows]


def create_run(
    conn: sqlite3.Connection,
    profile: dict[str, Any],
    task: str,
    project_ids: list[str],
    parent_run: str | None = None,
    goal: dict[str, Any] | None = None,
    outside: list[dict[str, Any]] | None = None,
) -> str:
    """The run's conversation, set up as the profile says: projects, rights, its fixed tool list, and its row.
    `goal`: an autonomous run's (D40) - goal, done_when, autonomy, max_replans, planner_model_id; it gets the
    autonomous tools besides the profile's, and `outside`: the outside MCP servers' tools ready now (D42)."""
    model = store.get_model(conn, profile["model_id"])
    if model is None or not model["enabled"]:
        raise AgentError(f"the agent's model {profile['model_id']!r} is not available now")
    projects = [get_project(conn, pid) for pid in project_ids]
    if model["locality"] == "cloud":
        local = [p.name for p in projects if not p.cloud_ok]
        if local:
            raise AgentError(f"{', '.join(local)}: only local models may see it (D10); the agent's model is cloud")
    title = " ".join(task.split())
    conversation_id = store.create_conversation(conn, kind="agent_run",
                                                title=f"{profile['name']}: {title[:60]}")  # fmt: skip
    store.set_conversation_projects(conn, conversation_id, project_ids)
    store.set_context_settings(conn, conversation_id, mode=profile["mode"])
    wanted = profile["tools"] or DEFAULT_TOOLS
    screen = computer.TOOLS if goal and goal.get("computer", True) else []  # a schedule's run: only if allowed (Q23)
    extra = autonomous.TOOLS + memory.TOOLS + agent_tools.TOOLS + mail.TOOLS + screen + (outside or []) if goal else []
    # sized to the model: one per name, no project tools without projects, short for a small window (hub/compact.py)
    window = (model.get("capabilities") or {}).get("context")
    tools = compact.for_model([t for t in AGENT_TOOLS if t["name"] in wanted] + extra, window,
                              projects=bool(project_ids))  # fmt: skip
    conn.execute("UPDATE conversations SET tools = ? WHERE id = ?", (json.dumps(tools), conversation_id))
    conn.execute(
        "INSERT INTO agent_runs (conversation_id, profile_id, profile_version, profile_name, instructions, model_id,"
        " max_steps, max_minutes, budget_usd, task, parent_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, profile["id"], profile["version"], profile["name"], profile["instructions"],
         profile["model_id"], profile["max_steps"], profile["max_minutes"], profile["budget_usd"], task, parent_run),
    )  # fmt: skip
    if goal:
        conn.execute(
            "UPDATE agent_runs SET kind = 'autonomous', goal = ?, done_when = ?, autonomy = ?, max_replans = ?,"
            " planner_model_id = ?, network = ?, schedule_id = ?, refusal_vector = ? WHERE conversation_id = ?",
            (goal["goal"], goal.get("done_when") or None, goal["autonomy"], goal["max_replans"],
             goal.get("planner_model_id") or None, int(bool(goal.get("network"))), goal.get("schedule_id"),
             None if goal.get("refusal_vector") is None else int(bool(goal["refusal_vector"])), conversation_id),
        )  # fmt: skip
    return conversation_id


def check_goal(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """An autonomous run's goal and limits (Q22's defaults: 100 steps, 60 minutes, $1, 3 new plans)."""
    goal = " ".join(str(data.get("goal") or "").split())
    if not goal:
        raise AgentError("an autonomous run needs a goal")
    if len(goal) > INSTRUCTIONS_CHARS:
        raise AgentError(f"a goal longer than {INSTRUCTIONS_CHARS} characters")
    autonomy = data.get("autonomy") or "plan"
    if autonomy not in autonomous.AUTONOMY:
        raise AgentError(f"unknown autonomy {autonomy!r}: {', '.join(autonomous.AUTONOMY)}")
    planner = str(data.get("planner_model_id") or "")
    if planner and (planner.startswith(f"{CLAUDE_CODE}/") or store.get_model(conn, planner) is None):
        raise AgentError(f"unknown planner model {planner!r}")
    vector = data.get("refusal_vector")
    if vector is not None and not isinstance(vector, bool):
        raise AgentError("refusal_vector: true, false or none (as the model is set)")
    defaults = autonomous.DEFAULTS
    budget = data.get("budget_usd", defaults["budget_usd"])
    if budget is not None and (not isinstance(budget, int | float) or budget <= 0):
        raise AgentError("a budget is a positive number of dollars, or none")
    return {"goal": goal, "done_when": " ".join(str(data.get("done_when") or "").split()), "autonomy": autonomy,
            "network": bool(data.get("network")), "computer": bool(data.get("computer", True)),
            "schedule_id": str(data.get("schedule_id") or "") or None,  # a schedule's run (9.7): may it use the screen
            "refusal_vector": vector,  # Strata's refusal vector for this task; None: as the model is set
            "planner_model_id": planner, "max_steps": _within(data.get("max_steps", defaults["max_steps"]), STEPS,
                                                              "max_steps"),
            "max_minutes": _within(data.get("max_minutes", defaults["max_minutes"]), MINUTES, "max_minutes"),
            "max_replans": _within(data.get("max_replans", defaults["max_replans"]), (0, 20), "max_replans"),
            "budget_usd": float(budget) if budget is not None else None}  # fmt: skip


def autonomous_profile(conn: sqlite3.Connection, model_id: str) -> dict[str, Any]:
    """The profile an autonomous run gets when none is chosen: every tool of the hub, "ask" rights - made once."""
    found = next((p for p in list_profiles(conn) if p["name"] == AUTONOMOUS_PROFILE), None)
    if found is not None:
        return found
    return save_profile(conn, {"name": AUTONOMOUS_PROFILE, "model_id": model_id, "tools": [], "mode": "ask",
                               "instructions": "", "max_steps": 100, "max_minutes": 60})  # fmt: skip


# --- a coordinator's batches (16.1, D50) ---


def _task(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) | {"dependencies": json.loads(row["dependencies"])}


def list_tasks(conn: sqlite3.Connection, coordinator: str, batch: int | None = None) -> list[dict[str, Any]]:
    """The coordinator's board: the tasks of its batches (or of one), in the order it wrote them."""
    rows = conn.execute(
        "SELECT * FROM delegated_tasks WHERE coordinator = ? AND (? IS NULL OR batch = ?) ORDER BY batch, position",
        (coordinator, batch, batch),
    ).fetchall()
    return [_task(r) for r in rows]


def save_batch(conn: sqlite3.Connection, coordinator: str, tasks: list[dict[str, Any]]) -> int:
    """The batch's rows, pending; its number. A task_id names one task of the coordinator's for good."""
    used = {r[0] for r in conn.execute("SELECT task_id FROM delegated_tasks WHERE coordinator = ?", (coordinator,))}
    again = next((t["task_id"] for t in tasks if t["task_id"] in used), None)
    if again is not None:
        raise AgentError(f"task_id {again!r} was used in an earlier batch of yours")
    number = conn.execute("SELECT COALESCE(MAX(batch), 0) + 1 FROM delegated_tasks WHERE coordinator = ?",
                          (coordinator,)).fetchone()[0]  # fmt: skip
    conn.executemany(
        "INSERT INTO delegated_tasks (coordinator, task_id, batch, position, agent, prompt, dependencies)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(coordinator, t["task_id"], number, n, t["agent_profile"], t["prompt"], json.dumps(t["dependencies"]))
         for n, t in enumerate(tasks)],
    )  # fmt: skip
    return number


def _mark(conn: sqlite3.Connection, coordinator: str, task_id: str, **fields: Any) -> None:
    conn.execute(
        f"UPDATE delegated_tasks SET {', '.join(f'{k} = ?' for k in fields)} WHERE coordinator = ? AND task_id = ?",
        (*fields.values(), coordinator, task_id),
    )


def board(tasks: list[dict[str, Any]]) -> str:
    """What the coordinator gets back: how many ended how, then each task with the agent's answer."""
    counts = collections.Counter(t["status"] for t in tasks)
    told = [f"{counts[s]} {s}" for s in ("done", "failed", "cancelled", "skipped", "running", "pending") if counts[s]]
    lines = [f"{len(tasks)} task{'' if len(tasks) == 1 else 's'}: {', '.join(told)}."]
    for t in tasks:
        head = f"[{t['task_id']}] «{t['agent']}» — {t['status']}"
        if t["status"] == "skipped":
            lines.append(f"{head}: {t['detail']}")
            continue
        head += f" ({t['detail']})" if t["detail"] and t["status"] != "done" else ""
        answer = t["answer"] or ""
        if len(answer) > ANSWER_CHARS:
            answer = answer[:ANSWER_CHARS] + "\n… (cut here; the whole answer is in the agent's run)"
        lines.append(f"{head}. Answer:\n{answer}" if answer else f"{head}.")
    return "\n\n".join(lines)


async def _either(first: asyncio.Event, second: asyncio.Event) -> None:
    waits = [asyncio.create_task(first.wait()), asyncio.create_task(second.wait())]
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waits:
            w.cancel()


class AgentRunner:
    """Starts runs, keeps the GPU queue (local models one at a time), records how they end, resumes them."""

    def __init__(self, db: Database, bus: EventBus, chat: ChatService):
        self._db = db
        self._bus = bus
        self._chat = chat
        self._gpu = asyncio.Semaphore(1)
        self._held: set[str] = set()  # runs holding the GPU now
        self._waiting: dict[str, asyncio.Task] = {}  # runs queued for the GPU
        self._ended: dict[str, asyncio.Event] = {}  # sub-runs a coordinator waits for

    async def start(self) -> list[str]:
        """Runs the hub was running (or had queued) when it stopped go on now; a batch's tasks that had not begun
        will not (nobody waits for them now); those running end as their runs do."""

        def unfinished_runs(conn) -> list[str]:
            conn.execute("UPDATE delegated_tasks SET status = 'cancelled', detail = ?, ended_at = ?"
                         " WHERE status = 'pending'", ("the hub restarted before it began", utc_now()))  # fmt: skip
            return [r["conversation_id"] for r in conn.execute(
                "SELECT conversation_id FROM agent_runs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()]  # fmt: skip

        unfinished = await self._db.acall(unfinished_runs)
        for run_id in unfinished:
            await self._launch(run_id, resume=True)
        return unfinished

    async def run(self, profile_id: str, task: str, project_ids: list[str] | None = None) -> dict[str, Any]:
        task = task.strip()
        if not task:
            raise AgentError("a run needs a task")

        def create(conn):
            profile = get_profile(conn, profile_id)
            return create_run(conn, profile, task, profile["project_ids"] if project_ids is None else project_ids)

        try:
            run_id = await self._db.acall(create)
        except ProjectError as exc:
            raise AgentError(str(exc)) from exc
        await self._launch(run_id, resume=False)
        return await self._db.acall(lambda conn: get_run(conn, run_id))

    async def run_autonomous(self, data: dict[str, Any]) -> dict[str, Any]:
        """An autonomous run (D40): a goal for a profile (or the built-in one), with the model, projects and limits
        asked for, else the profile's."""

        def create(conn):
            goal = check_goal(conn, data)
            model_id = str(data.get("model_id") or "")
            if data.get("profile_id"):
                profile = get_profile(conn, str(data["profile_id"]))
            else:
                if not model_id:
                    raise AgentError("choose the model that carries the goal out")
                profile = autonomous_profile(conn, model_id)
            profile = {**profile, "max_steps": goal["max_steps"], "max_minutes": goal["max_minutes"],
                       "budget_usd": goal["budget_usd"]}  # fmt: skip
            if model_id:
                check_profile(conn, {**profile, "model_id": model_id})  # a model a profile may have
                profile["model_id"] = model_id
            projects = data.get("project_ids")
            done_when = f"\nГотово, когда: {goal['done_when']}" if goal["done_when"] else ""  # i18n: keep
            task = f"Цель: {goal['goal']}{done_when}"  # the agent's task: as written  # i18n: keep
            return create_run(conn, profile, task, profile["project_ids"] if projects is None else
                              [str(p) for p in projects], goal=goal, outside=outside)  # fmt: skip

        outside = self._chat.mcp.definitions() if self._chat.mcp is not None else []
        try:
            run_id = await self._db.acall(create)
        except ProjectError as exc:
            raise AgentError(str(exc)) from exc
        await self._launch(run_id, resume=False)
        return await self._db.acall(lambda conn: get_run(conn, run_id))

    async def delegate(self, parent_id: str, args: dict[str, Any], cancel: asyncio.Event) -> tuple[str, bool]:
        """A coordinator's delegate_task: a sub-run of the named agent, waited for; its answer is the result."""
        name, task = str(args.get("agent") or "").strip(), str(args.get("task") or "").strip()
        if not name or not task:
            return "`agent` (a name) and `task` (what to do) are both needed", True

        def create(conn) -> str:
            if get_run(conn, parent_id)["parent_run"]:
                raise AgentError(ONE_LEVEL)
            found = next((p for p in list_profiles(conn) if p["name"].casefold() == name.casefold()), None)
            if found is None:
                names = ", ".join(p["name"] for p in list_profiles(conn)) or "none"
                raise AgentError(f"no agent {name!r}; the agents are: {names}")
            return create_run(conn, found, task, found["project_ids"], parent_run=parent_id)

        try:
            child_id = await self._db.acall(create)
        except (AgentError, ProjectError) as exc:
            return str(exc), True
        ended = self._ended[child_id] = asyncio.Event()
        gave_up = parent_id in self._held  # the GPU goes to whoever needs it while the coordinator waits
        if gave_up:
            self._held.discard(parent_id)
            self._gpu.release()
        try:
            await self._launch(child_id, resume=False)
            stopping = asyncio.create_task(cancel.wait())
            ending = asyncio.create_task(ended.wait())
            await asyncio.wait({stopping, ending}, return_when=asyncio.FIRST_COMPLETED)
            stopping.cancel()
            if not ended.is_set():  # the coordinator was stopped: so is its sub-run
                await self.cancel(child_id)
                await ended.wait()
            ending.cancel()
        finally:
            self._ended.pop(child_id, None)
            if gave_up:
                await self._gpu.acquire()
                self._held.add(parent_id)
        run, answer = await self._db.acall(lambda conn: (get_run(conn, child_id), _last_answer(conn, child_id)))
        how = {"done": "finished", "error": "failed", "cancelled": "was stopped"}.get(run["status"], run["status"])
        head = f"agent «{run['profile_name']}» {how}" + (f" ({run['detail']})" if run.get("detail") else "")
        return f"{head}. Its answer:\n{answer}" if answer else head, run["status"] != "done"

    async def delegate_many(self, parent_id: str, args: dict[str, Any], cancel: asyncio.Event) -> tuple[str, bool]:
        """A coordinator's delegate_tasks (16.1, D50): the batch checked whole, then run by its dependencies - cloud
        tasks at once, local ones one after another; the board when every task has ended."""
        try:
            tasks = check_batch(args)
        except BatchError as exc:
            return str(exc), True

        def prepare(conn):
            if get_run(conn, parent_id)["parent_run"]:
                raise AgentError(ONE_LEVEL)
            profiles = list_profiles(conn)
            by_name = {p["name"].casefold(): p for p in profiles}
            for task in tasks:
                if task["agent_profile"].casefold() not in by_name:
                    names = ", ".join(p["name"] for p in profiles) or "none"
                    raise AgentError(f"no agent {task['agent_profile']!r}; the agents are: {names}")
            found = {t["task_id"]: by_name[t["agent_profile"].casefold()] for t in tasks}
            for task in tasks:  # the board says the agent's own name
                task["agent_profile"] = found[task["task_id"]]["name"]
            local = {}
            for task_id, profile in found.items():
                model = store.get_model(conn, profile["model_id"])
                local[task_id] = model["id"] if model is not None and model["locality"] == "local" else None
            return found, local, save_batch(conn, parent_id, tasks)

        try:
            profiles, local, number = await self._db.acall(prepare)
        except AgentError as exc:
            return str(exc), True
        ordered = order(tasks, lambda t: local[t["task_id"]])
        ends = {t["task_id"]: asyncio.Event() for t in tasks}
        before: dict[str, str | None] = {}  # a local task: the local task before it (the GPU, one after another)
        last = None
        for task in ordered:
            if local[task["task_id"]] is not None:
                before[task["task_id"]], last = last, task["task_id"]
        cloud = asyncio.Semaphore(CLOUD_AT_ONCE)
        gave_up = parent_id in self._held  # the GPU goes to the batch while the coordinator waits
        if gave_up:
            self._held.discard(parent_id)
            self._gpu.release()
        try:
            await asyncio.gather(*(
                self._batch_task(parent_id, t, profiles[t["task_id"]], ends, before.get(t["task_id"]),
                                 None if local[t["task_id"]] else cloud, cancel)
                for t in ordered))  # fmt: skip
        finally:
            if gave_up:
                await self._gpu.acquire()
                self._held.add(parent_id)
        ended = await self._db.acall(lambda conn: list_tasks(conn, parent_id, number))
        return board(ended), any(t["status"] != "done" for t in ended)

    async def _batch_task(self, parent_id: str, task: dict[str, Any], profile: dict[str, Any],
                          ends: dict[str, asyncio.Event], before: str | None, cloud: asyncio.Semaphore | None,
                          cancel: asyncio.Event) -> None:  # fmt: skip
        """One task of a batch: waits for its dependencies (and a local one for the local task before it), then runs
        as a sub-run of the coordinator; its row says how it went (`_finish` writes a run's end)."""
        task_id = task["task_id"]
        child_id = None
        try:
            for waited in [*task["dependencies"], *([before] if before else [])]:
                await _either(ends[waited], cancel)
            if cancel.is_set():
                await self._db.acall(lambda conn: _mark(conn, parent_id, task_id, status="cancelled",
                                                        ended_at=utc_now()))  # fmt: skip
                return

            def start(conn) -> str | None:
                rows = {t["task_id"]: t for t in list_tasks(conn, parent_id)}
                words = {"failed": "failed", "skipped": "was skipped", "cancelled": "was cancelled"}
                missed = [f"{d} {words.get(rows[d]['status'], rows[d]['status'])}" for d in task["dependencies"]
                          if rows[d]["status"] != "done"]  # fmt: skip
                if missed:
                    _mark(conn, parent_id, task_id, status="skipped", detail="; ".join(missed), ended_at=utc_now())
                    return None
                found = [f"[{d}] «{rows[d]['agent']}»:\n{rows[d]['answer'] or '(no answer)'}"
                         for d in task["dependencies"]]  # fmt: skip
                prompt = task["prompt"] + ("\n\nWhat the tasks this one waited for found:\n\n" + "\n\n".join(found)
                                           if found else "")  # fmt: skip
                try:
                    run_id = create_run(conn, profile, prompt, profile["project_ids"], parent_run=parent_id)
                except (AgentError, ProjectError) as exc:
                    _mark(conn, parent_id, task_id, status="failed", detail=str(exc), ended_at=utc_now())
                    return None
                _mark(conn, parent_id, task_id, status="running", run_id=run_id, started_at=utc_now())
                return run_id

            if cloud is not None:
                await cloud.acquire()
            try:
                child_id = await self._db.acall(start)
                if child_id is None:
                    return
                ended = self._ended[child_id] = asyncio.Event()
                await self._launch(child_id, resume=False)
                await _either(ended, cancel)
                if not ended.is_set():  # the coordinator was stopped: so is this task
                    await self.cancel(child_id)
                    await ended.wait()
            finally:
                if cloud is not None:
                    cloud.release()
        finally:
            if child_id is not None:
                self._ended.pop(child_id, None)
            ends[task_id].set()

    async def cancel(self, run_id: str) -> bool:
        queued = self._waiting.pop(run_id, None)
        if queued is not None:  # still waiting for the GPU: it never starts
            queued.cancel()
            await self._finish(run_id, Outcome(status="cancelled"))
            return True
        return self._chat.cancel(run_id)

    async def _launch(self, run_id: str, *, resume: bool) -> None:
        run = await self._db.acall(lambda conn: get_run(conn, run_id))
        model = await self._db.acall(lambda conn: store.get_model(conn, run["model_id"]))
        local = model is not None and model["locality"] == "local"
        task = asyncio.create_task(self._go(run, local=local, resume=resume))
        if local:
            self._waiting[run_id] = task
        task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _go(self, run: dict[str, Any], *, local: bool, resume: bool) -> None:
        run_id = run["conversation_id"]
        await self._status(run_id, "queued")
        if local:
            await self._gpu.acquire()  # one local run at a time: the GPU holds one model (D16)
            self._waiting.pop(run_id, None)
            self._held.add(run_id)
        auto = run.get("kind") == "autonomous"
        limits = Limits(max_steps=run["max_steps"], deadline=time.monotonic() + run["max_minutes"] * 60,
                        budget_usd=run["budget_usd"], autonomous=auto)  # fmt: skip
        model_id = run["model_id"]
        if auto and run.get("planner_model_id"):  # the planner plans; once the plan is made, the run's model goes on
            planned = await self._db.acall(lambda conn: autonomous.current_plan(conn, run_id) is not None)
            model_id = run["model_id"] if planned else run["planner_model_id"]
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE agent_runs SET status = 'running', started_at = COALESCE(started_at, ?) WHERE conversation_id = ?",
            (utc_now(), run_id)))  # fmt: skip
        current = await self._db.acall(lambda conn: get_run(conn, run_id))
        await self._bus.publish("run.started", {"run": current, "resumed": resume})
        text = RESUMED if resume else run["task"]
        try:
            await self._chat.start(run_id, model_id, text, limits=limits,
                                   on_end=lambda outcome: self._finish(run_id, outcome))  # fmt: skip
        except GatewayError as exc:
            await self._finish(run_id, Outcome(status="error", detail=str(exc)))
        except Exception:
            log.exception("run %s did not start", run_id)
            await self._finish(run_id, Outcome(status="error", detail="internal error"))

    async def _finish(self, run_id: str, outcome: Outcome) -> None:
        if run_id in self._held:
            self._held.discard(run_id)
            self._gpu.release()
        totals = outcome.totals
        status = outcome.status if outcome.status in ("done", "error", "cancelled") else "done"
        if self._chat.computer is not None:  # its hold on the desktop goes with it (D41)
            await self._chat.computer.release(run_id)
        autonomy = self._chat.autonomy
        if autonomy is not None:
            await autonomy.expire_all(run_id)  # its questions: nobody waits for the answers now
            autonomy.forget(run_id)
            info = await autonomy.info(run_id)
            if info is not None and info["report"] is None and status == "done" and not outcome.detail:
                outcome.detail = tr("закончил без отчёта")

        def write(conn):
            conn.execute(
                "UPDATE agent_runs SET status = ?, detail = ?, steps = steps + ?, cost_usd = cost_usd + ?,"
                " ended_at = ? WHERE conversation_id = ?",
                (status, outcome.detail or None, totals.steps if totals else 0, totals.cost if totals else 0.0,
                 utc_now(), run_id),
            )  # fmt: skip
            conn.execute(  # a batch's task: the board says how it ended (16.1)
                "UPDATE delegated_tasks SET status = ?, answer = ?, detail = ?, ended_at = ? WHERE run_id = ?",
                (ENDED[status], _last_answer(conn, run_id) or None, outcome.detail or None, utc_now(), run_id),
            )
            return get_run(conn, run_id)

        run = await self._db.acall(write)
        await self._bus.publish("run.finished", {"run": run})
        ended = self._ended.get(run_id)
        if ended is not None:
            ended.set()

    async def _status(self, run_id: str, status: str) -> None:
        await self._db.acall(
            lambda conn: conn.execute("UPDATE agent_runs SET status = ? WHERE conversation_id = ?", (status, run_id))
        )
        await self._bus.publish("run.status", {"run_id": run_id, "status": status}, persist=False)
