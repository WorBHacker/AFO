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
end done is skipped. The coordinator waits for the whole batch and gets the board back - with what each task changed
in its copies in short, and task_diff gives it whole (61.1): the architect looks at the work, not at file names.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from common.i18n import tr
from common.logging_setup import log_task_exception

from . import (
    agent_tools,
    approvals,
    autonomous,
    budgets,
    checks,
    compact,
    computer,
    goals,
    guard,
    judge,
    mail,
    memory,
    sandbox,
    store,  # fmt: skip
)
from .approvals import trusted_skips
from .autonomous import NOTE
from .chat import CHAT_TOOLS, ChatService, Limits, Outcome, _attached
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database, utc_now
from .delegate import (
    DELEGATE,
    DELEGATE_MANY,
    DIFF_PIECE,
    ROUNDS,
    TASK_DIFF,
    BatchError,
    check_batch,
    models_text,
    order,
    task_models,
)
from .events import EventBus
from .gateway import GatewayError
from .gateway.base import tool_result_block
from .projects import ProjectError, get_project
from .secret_filter import clean, is_secret_file
from .snapshot import PAGE_SNAPSHOT, SnapshotError
from .visual import GENERATE_IMAGE, GENERATE_VIDEO

log = logging.getLogger("hub.agents")

MODES = ("read_only", "ask", "trusted")
STEPS = (1, 200)
MINUTES = (1, 24 * 60)
NAME_CHARS = 60
INSTRUCTIONS_CHARS = 8000
AGENT_TOOLS = [*CHAT_TOOLS, DELEGATE, DELEGATE_MANY, GENERATE_IMAGE, GENERATE_VIDEO]  # what a profile chooses from
TOOL_NAMES = [t["name"] for t in AGENT_TOOLS]
COORDINATING = (DELEGATE["name"], DELEGATE_MANY["name"])
# "all": a coordinator is chosen, not by default; so are pictures (29.6) - they take the card from a local model
DEFAULT_TOOLS = [n for n in TOOL_NAMES if n not in (*COORDINATING, GENERATE_IMAGE["name"], GENERATE_VIDEO["name"])]
CLOUD_AT_ONCE = 4  # a batch's cloud tasks running together: the providers' request limits
ANSWER_CHARS = 6000  # of each answer on the coordinator's board
CHANGED_SHOWN = 12  # files named per project on the board
# the coordinator cannot put them there - live on 2026-11-05 it offered to (16.5)
WAITING_CHANGES = "\nIts changes wait in a sandbox, not in the project yet - only the user puts them there: "
DIFF_SHOWN = 3000  # characters of a task's changes on the board, in short (61.1); task_diff gives them whole
IN_SHORT = "\nWhat it changed, in short - the lines taken out (-) and put in (+); task_diff gives it whole:\n"
LOCAL_ONLY = "«{name}» is local-only (D10): its changes are not shown to a cloud model\n"
LOCAL_PAGES = "«{name}» is local-only (D10): its pages are not shown to a cloud model"
# (Стабилизация, 61.1) what a task of a local-only project found - its answer, its files, its checks' words - a cloud
# model is not told: the board, a dependent task's prompt, delegate_task's result say only how it ended
LOCAL_ANSWER = (
    "it worked with {names} - local-only (D10): its answer, files and checks are not shown to a cloud "
    "model; the user sees them in AFO"
)
SECRET_PART = "(a file of secrets: its lines are not shown)\n"
RESERVE_USD = 0.5  # what a batch keeps for a paid task whose agent has no budget of its own (16.4)
CONFLICTED = ("\n\nThe files of your copy of the project carry conflict marks where the tasks you waited for changed "
              "the same place - mend them first: ")  # fmt: skip
ONE_LEVEL = "you are a sub-agent yourself: you cannot give tasks further (one level, D20)"
ENDED = {"done": "done", "error": "failed", "cancelled": "cancelled"}  # a run's end -> its task's
# a task's statuses, as the board counts them: checking - its run is done, the hub checks it; escalated - its check
# did not pass in ROUNDS rounds, or cannot be made: a person is needed (16.3)
STATUSES = ("done", "escalated", "failed", "cancelled", "skipped", "checking", "running", "pending")
MISSED = {"failed": "failed", "skipped": "was skipped", "cancelled": "was cancelled", "escalated": "needs a person"}
DONE_WHEN = "\n\nDONE WHEN (the hub checks it when you end; you cannot declare it done):\n"
FILES_NOTE = "\n\nTHE FILES OF THIS TASK - their text is in your context (read_file gives more of a long one): "
UNCHECKED = "the hub restarted before the task was checked: nobody checks it now"
RESUMED = "[AFO hub] The hub restarted while you worked on this task. Look at where you stopped and go on."
BATCH_RESUMED = ("[AFO hub] The hub restarted while your batch of tasks ran; the batch went on, and its board is your "
                 "delegate_tasks call's result now. Go on from there.")  # fmt: skip
NOT_KEPT = "the hub restarted during this turn: what this call did, if anything, was not kept"
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
    chosen = [t for t in AGENT_TOOLS if t["name"] in wanted]
    if DELEGATE_MANY["name"] in wanted:  # who gives batches reads what their tasks changed, whole (61.1)
        chosen.append(TASK_DIFF)
        if (model.get("capabilities") or {}).get("vision"):  # and sees the pages they made (61.7)
            chosen.append(PAGE_SNAPSHOT)
    # sized to the model: one per name, no project tools without projects, short for a small window (hub/compact.py)
    window = (model.get("capabilities") or {}).get("context")
    tools = compact.for_model(chosen + extra, window, projects=bool(project_ids))
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
            " planner_model_id = ?, network = ?, schedule_id = ?, refusal_vector = ?, screen_cloud = ?"
            " WHERE conversation_id = ?",
            (goal["goal"], goal.get("done_when") or None, goal["autonomy"], goal["max_replans"],
             goal.get("planner_model_id") or None, int(bool(goal.get("network"))), goal.get("schedule_id"),
             None if goal.get("refusal_vector") is None else int(bool(goal["refusal_vector"])),
             int(bool(goal.get("screen_cloud"))), conversation_id),
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
    screen = data.get("screen_cloud")
    if screen is not None and not isinstance(screen, bool):
        raise AgentError("screen_cloud: true, false or none (as the switch for new tasks says)")
    defaults = autonomous.DEFAULTS
    budget = data.get("budget_usd", defaults["budget_usd"])
    if budget is not None and (not isinstance(budget, int | float) or budget <= 0):
        raise AgentError("a budget is a positive number of dollars, or none")
    return {"goal": goal, "done_when": " ".join(str(data.get("done_when") or "").split()), "autonomy": autonomy,
            "network": bool(data.get("network")), "computer": bool(data.get("computer", True)),
            "schedule_id": str(data.get("schedule_id") or "") or None,  # a schedule's run (9.7): may it use the screen
            "refusal_vector": vector,  # Strata's refusal vector for this task; None: as the model is set
            # a cloud model reads other programs' windows (Q20, 22.3); not said: the switch is the default
            "screen_cloud": agent_tools.screen_cloud(conn) if screen is None else screen,
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
    return dict(row) | {"dependencies": json.loads(row["dependencies"]),
                        "criteria": json.loads(row["criteria"]) if row["criteria"] else [],
                        "files": json.loads(row["files"]) if row["files"] else []}  # fmt: skip


def list_tasks(conn: sqlite3.Connection, coordinator: str, batch: int | None = None) -> list[dict[str, Any]]:
    """The coordinator's board: the tasks of its batches (or of one), in the order it wrote them; `sandbox` - the
    task's run writes into copies of its projects (16.2); its run's model, steps, cost and status, and `waiting` -
    the run waits for the user's answer (16.4)."""
    rows = conn.execute(
        "SELECT t.*, COALESCE(c.mode = 'sandbox', 0) AS sandbox, r.model_id, r.steps, r.cost_usd,"
        " r.status AS run_status, EXISTS (SELECT 1 FROM approvals a WHERE a.conversation_id = t.run_id AND"
        " a.status = 'pending') AS waiting FROM delegated_tasks t"
        " LEFT JOIN conversation_context c ON c.conversation_id = t.run_id"
        " LEFT JOIN agent_runs r ON r.conversation_id = t.run_id"
        " WHERE t.coordinator = ? AND (? IS NULL OR t.batch = ?) ORDER BY t.batch, t.position",
        (coordinator, batch, batch),
    ).fetchall()
    return [_task(r) | {"sandbox": bool(r["sandbox"]), "waiting": bool(r["waiting"]), "steps": r["steps"] or 0,
                        "cost_usd": r["cost_usd"] or 0.0} for r in rows]  # fmt: skip


def _check_ticked(folder: Path, sandboxed: list[tuple[dict[str, Any], list]], files: dict[str, dict[str, list[str]]],
                  hunks: dict[str, dict[str, dict[str, list[int]]]]) -> None:  # fmt: skip
    """The files and hunks ticked at a coordinator (17.1, 17.4): each task, project, file and hunk there - or
    SandboxError, before any is applied."""
    by_task = {t["task_id"]: (t, projects) for t, projects in sandboxed}
    for task_id in {*files, *hunks}:
        if task_id not in by_task:
            raise sandbox.SandboxError(f"no task {task_id!r} with a sandbox")
        task, projects = by_task[task_id]
        for project_id in {*files.get(task_id, {}), *hunks.get(task_id, {})}:
            project = next((p for p in projects if p.id == project_id), None)
            if project is None:
                raise sandbox.SandboxError(f"task {task_id!r} has no sandbox of project {project_id!r}")
            there = sandbox.changes(folder, task["run_id"], project)["files"]
            only = files.get(task_id, {}).get(project_id) if task_id in files else None
            sandbox.parts(there if only is None else sandbox.chosen(there, only),
                          hunks.get(task_id, {}).get(project_id, {}))  # fmt: skip


def applying_order(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tasks as they ran: batch after batch, each task after those it waited for - the order their changes go
    onto the project (each task's own diff begins where those before it ended, 16.2)."""
    out: list[dict[str, Any]] = []
    for number in dict.fromkeys(t["batch"] for t in tasks):
        out += order([t for t in tasks if t["batch"] == number], lambda _t: None)
    return out


def save_batch(conn: sqlite3.Connection, coordinator: str, tasks: list[dict[str, Any]]) -> int:
    """The batch's rows, pending; its number. A task_id names one task of the coordinator's for good."""
    used = {r[0] for r in conn.execute("SELECT task_id FROM delegated_tasks WHERE coordinator = ?", (coordinator,))}
    again = next((t["task_id"] for t in tasks if t["task_id"] in used), None)
    if again is not None:
        raise AgentError(f"task_id {again!r} was used in an earlier batch of yours")
    number = conn.execute("SELECT COALESCE(MAX(batch), 0) + 1 FROM delegated_tasks WHERE coordinator = ?",
                          (coordinator,)).fetchone()[0]  # fmt: skip
    conn.executemany(
        "INSERT INTO delegated_tasks (coordinator, task_id, batch, position, agent, prompt, dependencies, criteria,"
        " files, given_model_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(coordinator, t["task_id"], number, n, t["agent_profile"], t["prompt"], json.dumps(t["dependencies"]),
          json.dumps(t["criteria"], ensure_ascii=False) if t.get("criteria") else None,
          json.dumps(t["files"], ensure_ascii=False) if t.get("files") else None, t.get("given_model_id"))
         for n, t in enumerate(tasks)],
    )  # fmt: skip
    return number


def task_profile(conn: sqlite3.Connection, task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """What a task runs as (61.3): its agent's profile - on the model the task names, when it names one, or
    AgentError: a model the hub offers (by its id or name), a cloud one only for projects the cloud may see (D10)."""
    wanted = task.get("model") or ""
    if not wanted:
        return profile
    models = task_models(conn)
    found = next((m for m in models if m["id"] == wanted), None) or next(
        (m for m in models if wanted.casefold() in (m["id"].casefold(), m["name"].casefold())), None)  # fmt: skip
    if found is None:
        raise AgentError(f"task {task['task_id']!r}: no model {wanted!r} to run it on; the models: "
                         f"{models_text(models)}")  # fmt: skip
    if found["locality"] == "cloud":
        local = []
        for pid in profile["project_ids"]:
            try:
                project = get_project(conn, pid)
            except ProjectError:
                continue
            local += [] if project.cloud_ok else [project.name]
        if local:
            raise AgentError(f"task {task['task_id']!r}: {', '.join(local)} - only local models may see it (D10), "
                             f"and {found['id']} is a cloud one")  # fmt: skip
    return {**profile, "model_id": found["id"]}


def task_files(conn: sqlite3.Connection, task: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, str]]:
    """A task's own files (61.2) in its agent's projects - {project_id, path, project} each - or AgentError: a path
    inside a project, not a file of secrets; «project: path» when the agent has several projects."""
    attached = []
    for pid in profile["project_ids"]:
        try:
            attached.append(get_project(conn, pid))
        except ProjectError:
            continue
    if task.get("files") and not attached:
        raise AgentError(f"task {task['task_id']!r} names files, but its agent «{profile['name']}» has no project")
    by_name = {p.name.casefold(): p for p in attached}
    out = []
    for given in task.get("files") or []:
        name, colon, rest = given.partition(":")
        project = by_name.get(name.strip().casefold()) if colon else None
        if project is None and len(attached) > 1:
            names = ", ".join(f"«{p.name}»" for p in attached)
            raise AgentError(f"task {task['task_id']!r}: say whose «{given}» is - «project: path»; its agent has "
                             f"{names}")  # fmt: skip
        path = (rest if project is not None else given).replace("\\", "/").strip().strip("/")
        project = project or attached[0]
        if not path or ".." in path.split("/") or ":" in path:
            raise AgentError(f"task {task['task_id']!r}: «{given}» is not a path inside a project")
        if is_secret_file(path):
            raise AgentError(f"task {task['task_id']!r}: «{given}» holds secrets - a model never gets it")
        out.append({"project_id": project.id, "path": path, "project": project.name})
    return out


def _mark(conn: sqlite3.Connection, coordinator: str, task_id: str, **fields: Any) -> None:
    conn.execute(
        f"UPDATE delegated_tasks SET {', '.join(f'{k} = ?' for k in fields)} WHERE coordinator = ? AND task_id = ?",
        (*fields.values(), coordinator, task_id),
    )


def diff_files(diff: str, paths: list[str]) -> list[tuple[str, str]]:
    """A project's diff file by file - (path, its part): git gives the parts in the order of its files (61.1). A diff
    cut short (sandbox.DIFF_MAX) gives what it has."""
    parts = [p for p in re.split(r"(?m)^(?=diff --git )", diff) if p.startswith("diff --git ")]
    return list(zip(paths, parts, strict=False))


def safe_part(path: str, part: str) -> str:
    """A file's part of a diff as a model may see it: a file of secrets by its head only, any other with the secrets
    in it cut out (hub/secret_filter.py)."""
    if is_secret_file(path):
        return part.split("\n", 1)[0] + "\n" + SECRET_PART
    return clean(part)


def in_short(part: str) -> str:
    """Only what changed: the file's head, each change's @@ line, the lines taken out and put in - none around."""
    heads = ("diff --git", "new file", "deleted file", "Binary files", "@@", "+", "-", "(")
    kept = [line for line in part.splitlines() if line.startswith(heads)]
    return "\n".join(kept) + "\n" if kept else ""


def changes_in_short(project: dict[str, Any], cloud: bool) -> str:
    """A project's changes in a task's copy, as the board shows them (61.1); a cloud coordinator is not shown those
    of a local-only project (D10)."""
    if cloud and not project.get("cloud_ok", True):
        return LOCAL_ONLY.format(name=project["name"])
    paths = [f["path"] for f in project["files"]]
    return "".join(in_short(safe_part(path, part)) for path, part in diff_files(project["diff"], paths))


def local_names(conn: sqlite3.Connection, run_id: str | None) -> list[str]:
    """The local-only projects a run worked with (D10), «named»: what it found is not told to a cloud model."""
    return [f"«{p.name}»" for p in _attached(conn, run_id) if not p.cloud_ok] if run_id else []


def board(tasks: list[dict[str, Any]], changed: dict[str, list[dict[str, Any]]] | None = None,
          hidden: dict[str, list[str]] | None = None) -> str:  # fmt: skip
    """What the coordinator gets back: how many ended how, then each task with the agent's answer and, for one that
    wrote into a sandbox, the files it changed there - per project (16.2) - and the changes in short (61.1):
    `changed` - per task, {name, paths, short} per project. `hidden` - the tasks of local-only projects, for a cloud
    coordinator (D10): only how each ended."""
    counts = collections.Counter(t["status"] for t in tasks)
    told = [f"{counts[s]} {s}" for s in STATUSES if counts[s]]
    lines = [f"{len(tasks)} task{'' if len(tasks) == 1 else 's'}: {', '.join(told)}."]
    for t in tasks:
        head = f"[{t['task_id']}] «{t['agent']}» — {t['status']}"
        if t["status"] == "skipped":
            lines.append(f"{head}: {t['detail']}")
            continue
        if t["task_id"] in (hidden or {}):
            lines.append(f"{head}: " + LOCAL_ANSWER.format(names=", ".join(hidden[t["task_id"]])))
            continue
        if t["status"] == "done" and t.get("verdict"):  # its check passed (16.3)
            head += f" (checked: {t['detail']})"
        elif t["detail"] and t["status"] != "done":
            head += f" ({t['detail']})"
        if t["status"] == "escalated" and t.get("feedback"):
            head += f". The judge's last word: {t['feedback']}"
        answer = t["answer"] or ""
        if len(answer) > ANSWER_CHARS:
            answer = answer[:ANSWER_CHARS] + "\n… (cut here; the whole answer is in the agent's run)"
        entry = f"{head}. Answer:\n{answer}" if answer else f"{head}."
        mine = (changed or {}).get(t["task_id"], [])
        files = [f"«{c['name']}»: {', '.join(c['paths'][:CHANGED_SHOWN])}"
                 + (f" and {len(c['paths']) - CHANGED_SHOWN} more" if len(c["paths"]) > CHANGED_SHOWN else "")
                 for c in mine]  # fmt: skip
        if files:
            entry += WAITING_CHANGES + "; ".join(files)
        short = "".join(c["short"] for c in mine)
        if short:
            if len(short) > DIFF_SHOWN:
                short = short[:DIFF_SHOWN] + "\n… (cut here; task_diff gives the rest)"
            entry += IN_SHORT + short.rstrip("\n")
        lines.append(entry)
    return "\n\n".join(lines)


def _criteria(conn: sqlite3.Connection, task: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """A task's criteria checked as a goal's are (hub/goals.py), against the agent's projects; numbered, with the
    project's name - as the worker's word and the judge read them."""
    attached = []
    for pid in profile["project_ids"]:
        try:
            attached.append(get_project(conn, pid))
        except ProjectError:
            continue
    raw = task["criteria"]
    if isinstance(raw, list):  # {"checks": true} - the project's own checks, found by its files (61.4)
        raw = [c for item in raw for c in (_own_checks(task, item, attached)
                                           if isinstance(item, dict) and item.get("checks") else [item])]  # fmt: skip
    try:
        checked = goals.check_criteria(raw, attached)
    except goals.GoalError as exc:
        raise AgentError(f"task {task['task_id']!r}: {exc}") from None
    names = {p.id: p.name for p in attached}
    return [c | {"n": n, "project_name": names.get(c.get("project_id"), ""), "path": c.get("path", "")}
            for n, c in enumerate(checked, 1)]  # fmt: skip


def task_checks(task: dict[str, Any]) -> dict[str, set[str]]:
    """A task's check commands by project: allowed with its batch, its agent runs them in its copy unasked (61.4)."""
    out: dict[str, set[str]] = {}
    for c in task.get("criteria") or []:
        if c.get("kind") == "command":
            out.setdefault(c["project_id"], set()).add(c["command"])
    return out


def _own_checks(task: dict[str, Any], item: dict[str, Any], attached: list) -> list[dict[str, Any]]:
    """A criterion {"checks": true, "project"?} as the commands the hub found by the project's files (61.4), or
    AgentError: which project, and that it has checks the hub knows."""
    ref = str(item.get("project") or "").strip()
    project = next((p for p in attached if ref and (p.id == ref or p.name.casefold() == ref.casefold())), None)
    if project is None and not ref and len(attached) == 1:
        project = attached[0]
    if project is None:
        names = ", ".join(f"«{p.name}»" for p in attached) or "none"
        raise AgentError(f"task {task['task_id']!r}: `checks` - name the project, one of its agent's ({names})")
    found = checks.criteria(project)
    if not found:
        raise AgentError(f"task {task['task_id']!r}: the hub found no checks of «{project.name}» it can run (a "
                         "Python project's pytest, ruff or mypy, cargo, go - with their programs on this computer): "
                         "give the command itself")  # fmt: skip
    return found


def _short_of_money(conn: sqlite3.Connection, tasks: list[dict[str, Any]], found: dict[str, dict]) -> str | None:
    """A batch keeps money first (16.4): D12 checks a budget by what is spent, and paid tasks running at once could
    go over it together. Each paid cloud task counts its agent's own budget (its run stops there), or RESERVE_USD;
    the sum under each budget with a hard limit must fit what that budget has left. None - it fits; else why not."""
    need: dict[int, float] = {}
    under: dict[int, dict[str, Any]] = {}
    for task in tasks:
        profile = found[task["task_id"]]
        model = store.get_model(conn, profile["model_id"])
        if model is None or model["locality"] != "cloud" or not model["prices"]:
            continue
        for kept in budgets.statuses(conn, model["id"]):
            if kept["hard_usd"] is not None:
                need[kept["id"]] = need.get(kept["id"], 0.0) + (profile["budget_usd"] or RESERVE_USD)
                under[kept["id"]] = kept
    for budget_id, amount in need.items():
        kept = under[budget_id]
        left = max(kept["hard_usd"] - kept["spent_usd"], 0.0)
        if amount > left + 1e-9:
            name = f"{kept['scope_ref'] or kept['scope']} · {kept['period']}"
            return (f"the batch's paid tasks may spend up to ${amount:.2f} under the budget «{name}» - "
                    f"${left:.2f} of ${kept['hard_usd']:.2f} is left: give fewer paid tasks, a smaller budget in "
                    "their agents' profiles, or ask the user to raise the budget")  # fmt: skip
    return None


def round_note(task: dict[str, Any], found: dict[str, Any], round_no: int) -> str:
    """The worker's word after a check that found its task short (16.3): what is missing, the judge's word."""
    return (f'{NOTE} <task_check round="{round_no}" of="{ROUNDS}">\n'
            f"The hub checked your task: not done yet - this is round {round_no} of {ROUNDS}. Fix what is missing; "
            "the check runs again when you end. After the last round a person looks at it.\n"
            f"YOUR TASK: {task['prompt'][:2000]}\n" + goals.check_text(found) + "</task_check>")  # fmt: skip


def _answer_batch_call(conn: sqlite3.Connection, coordinator: str, said: str, error: bool) -> store.MessageRow | None:
    """The coordinator's delegate_tasks call the restart left unanswered gets the batch's board (61.5); the other
    calls of that turn, that what they did was not kept - one message of results, as after any turn of tools. None:
    there is no such call."""
    messages = store.get_messages(conn, coordinator)
    answered = {b.get("id") for m in messages if m.role == "tool" for b in m.content}
    last = next((m for m in reversed(messages) if m.role == "assistant"), None)
    calls = [b for b in (last.content if last else []) if b.get("type") == "tool_call" and b.get("id") not in answered]
    batch = [b for b in calls if b.get("name") == DELEGATE_MANY["name"]]
    if not batch:
        return None
    results = [tool_result_block(b["id"], b["name"], said, error) if b is batch[-1]
               else tool_result_block(b["id"], b.get("name", ""), NOT_KEPT, True) for b in calls]  # fmt: skip
    return store.add_message(conn, coordinator, "tool", results)


def _cloud(conn: sqlite3.Connection, run_id: str) -> bool:
    """Whether the run's model answers in the cloud: it is not shown local-only projects' changes (D10, 61.1)."""
    model = store.get_model(conn, get_run(conn, run_id)["model_id"])
    return model is not None and model["locality"] == "cloud"


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
        self._held: set[str] = set()  # runs holding the GPU now
        self._waiting: dict[str, asyncio.Task] = {}  # runs not under way yet: queued for the GPU, or getting ready
        self._ended: dict[str, asyncio.Event] = {}  # sub-runs a coordinator waits for
        self._resumed: dict[str, asyncio.Event] = {}  # coordinators whose batch goes on after a restart: their «Стоп»
        self._resuming: set[asyncio.Task] = set()
        self.goals = None  # set by the server: the goals' checks check a batch's tasks too (16.3)

    @property
    def _gpu(self) -> asyncio.Semaphore:
        """The card's one queue of work without the person (61.8): a local run waits there behind a goal's turn or
        the judge as behind another run."""
        return self._chat.card.lane

    async def start(self) -> list[str]:
        """Runs the hub was running (or had queued) when it stopped go on now. A batch whose coordinator still waits
        for it goes on too (61.5): its tasks not begun start by their dependencies, those running end as their runs
        do, those being checked are checked again; its board is then the answer to the coordinator's call, and the
        coordinator goes on with it. Of a batch nobody waits for, the tasks not begun will not, those being checked
        need a person."""

        def unfinished_runs(conn) -> tuple[list[str], list[tuple[str, int, list[dict[str, Any]]]]]:
            runs = [r["conversation_id"] for r in conn.execute(
                "SELECT conversation_id FROM agent_runs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()]  # fmt: skip
            open_batches = conn.execute(
                "SELECT coordinator, MAX(batch) FROM delegated_tasks WHERE status IN ('pending', 'running', 'checking')"
                " GROUP BY coordinator"
            ).fetchall()
            going = [(c, b) for c, b in open_batches if c in runs]  # the last batch of a coordinator that waits
            for row in conn.execute("SELECT coordinator, batch, task_id, status, criteria FROM delegated_tasks WHERE"
                                    " status IN ('pending', 'running', 'checking')").fetchall():  # fmt: skip
                if (row["coordinator"], row["batch"]) in going:
                    continue
                if row["status"] == "pending":  # nobody waits for it now
                    _mark(conn, row["coordinator"], row["task_id"], status="cancelled",
                          detail="the hub restarted before it began", ended_at=utc_now())  # fmt: skip
                elif row["status"] == "checking" or row["criteria"]:
                    _mark(conn, row["coordinator"], row["task_id"], status="escalated", detail=UNCHECKED,
                          ended_at=utc_now())  # fmt: skip
            return runs, [(c, b, list_tasks(conn, c, b)) for c, b in going]

        unfinished, batches = await self._db.acall(unfinished_runs)
        coordinators = {c for c, _, _ in batches}
        for coordinator, _, tasks in batches:  # set before any run goes on: whoever waits hears each end
            self._resumed[coordinator] = asyncio.Event()
            for task in tasks:
                if task["status"] == "running" and task["run_id"]:
                    self._ended[task["run_id"]] = asyncio.Event()
        for run_id in unfinished:
            if run_id not in coordinators:  # a coordinator goes on once its batch has ended
                await self._launch(run_id, resume=True)
        for coordinator, number, tasks in batches:
            going = asyncio.create_task(self._resume_batch(coordinator, number, tasks))
            self._resuming.add(going)
            going.add_done_callback(self._resuming.discard)
            going.add_done_callback(log_task_exception)
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
        try:
            run, answer, names = await self._db.acall(lambda conn: (
                get_run(conn, child_id), _last_answer(conn, child_id),
                local_names(conn, child_id) if _cloud(conn, parent_id) else []))  # fmt: skip
        except AgentError:  # its chat was deleted meanwhile, and the run with it
            return "the sub-run's chat was deleted: it was stopped, and its answer is gone", True
        how = {"done": "finished", "error": "failed", "cancelled": "was stopped"}.get(run["status"], run["status"])
        if names:  # a cloud coordinator, a local-only project's sub-run: how it ended only (D10)
            said = f"agent «{run['profile_name']}» {how}: " + LOCAL_ANSWER.format(names=", ".join(names))
            return said, run["status"] != "done"
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
            for task in tasks:  # on the model the task names (61.3): money and the GPU's queue go by it below
                found[task["task_id"]] = task_profile(conn, task, found[task["task_id"]])
                task["given_model_id"] = found[task["task_id"]]["model_id"] if task.get("model") else None
            local = {}
            for task_id, profile in found.items():
                model = store.get_model(conn, profile["model_id"])
                local[task_id] = model["id"] if model is not None and model["locality"] == "local" else None
            for task in tasks:  # its own files (61.2): in its agent's projects
                task["files"] = task_files(conn, task, found[task["task_id"]])
            for task in tasks:  # how the hub checks each (16.3): against the agent's own projects
                if task["criteria"]:
                    task["criteria"] = _criteria(conn, task, found[task["task_id"]])
            short = _short_of_money(conn, tasks, found)
            if short:
                raise AgentError(f"nothing started: {short}")
            return found, local, store.context_settings(conn, parent_id)["mode"], _cloud(conn, parent_id)

        if any(t["criteria"] for t in tasks) and self.goals is None:
            return "the hub cannot check tasks here: give the batch without `criteria`", True
        try:
            profiles, local, mode, cloud_here = await self._db.acall(prepare)
        except AgentError as exc:
            return str(exc), True
        refused = await self._allow_checks(parent_id, tasks, mode, cancel)
        if refused:
            return f"nothing started: the checks' commands were not allowed ({refused})", True
        try:
            number = await self._db.acall(lambda conn: save_batch(conn, parent_id, tasks))
        except AgentError as exc:
            return str(exc), True
        await self._told(parent_id)
        ends = {t["task_id"]: asyncio.Event() for t in tasks}
        gave_up = parent_id in self._held  # the GPU goes to the batch while the coordinator waits
        if gave_up:
            self._held.discard(parent_id)
            self._gpu.release()
        try:
            await self._go_through(parent_id, order(tasks, lambda t: local[t["task_id"]]), [], profiles, local, ends,
                                   cancel)  # fmt: skip
        finally:
            if gave_up:
                await self._gpu.acquire()
                self._held.add(parent_id)
        return await self._board_of(parent_id, number, cloud_here)

    async def _go_through(self, parent_id: str, new: list[dict[str, Any]], under_way: list[dict[str, Any]],
                          profiles: dict[str, dict[str, Any] | None], local: dict[str, str | None],
                          ends: dict[str, asyncio.Event], cancel: asyncio.Event) -> None:  # fmt: skip
        """A batch's tasks to their ends: `new` ones in an order they can start in - cloud ones CLOUD_AT_ONCE at a
        time, a local one after the local one before it (the GPU, one after another) -, and those `under_way` when
        the hub restarted (61.5)."""
        before: dict[str, str | None] = {}  # a local task: the local task before it
        last = None
        for task in new:
            if local[task["task_id"]] is not None:
                before[task["task_id"]], last = last, task["task_id"]
        cloud = asyncio.Semaphore(CLOUD_AT_ONCE)
        await asyncio.gather(
            *(self._batch_task(parent_id, t, profiles[t["task_id"]], ends, before.get(t["task_id"]),
                               None if local[t["task_id"]] else cloud, cancel) for t in new),
            *(self._batch_task(parent_id, t, None, ends, None, None, cancel, resumed=True) for t in under_way),
        )  # fmt: skip

    async def _board_of(self, parent_id: str, number: int, cloud_here: bool) -> tuple[str, bool]:
        """The board of a batch that ended - what the coordinator gets - and whether a task of it did not end done; a
        cloud coordinator hears of a local-only project's task only how it ended (D10)."""

        def read(conn):
            ended = list_tasks(conn, parent_id, number)
            hidden = {t["task_id"]: names for t in ended if cloud_here and (names := local_names(conn, t["run_id"]))}
            return ended, hidden

        ended, hidden = await self._db.acall(read)
        made = await self.task_changes(parent_id, number)
        changed = await asyncio.to_thread(lambda: {
            t["task_id"]: [{"name": p["name"], "paths": [f["path"] for f in p["files"]],
                            "short": changes_in_short(p, cloud_here)} for p in t["projects"]]
            for t in made})  # fmt: skip
        return board(ended, changed, hidden), any(t["status"] != "done" for t in ended)

    async def _resume_batch(self, coordinator: str, number: int, rows: list[dict[str, Any]]) -> None:
        """A batch the hub was running when it stopped, its coordinator waiting for it (61.5): its tasks go on; its
        board answers the coordinator's call, and the coordinator goes on with it - or ends stopped, when the user
        stopped it meanwhile («Стоп»: `cancel`)."""
        cancel = self._resumed[coordinator]
        try:

            def prepare(conn):
                by_name = {p["name"]: p for p in list_profiles(conn)}
                profiles: dict[str, dict[str, Any] | None] = {}
                local: dict[str, str | None] = {}
                for t in rows:
                    profile = by_name.get(t["agent"])
                    if profile is not None and t["given_model_id"]:  # the model the task named (61.3)
                        profile = {**profile, "model_id": t["given_model_id"]}
                    model = store.get_model(conn, profile["model_id"]) if profile is not None else None
                    profiles[t["task_id"]] = profile
                    local[t["task_id"]] = model["id"] if model is not None and model["locality"] == "local" else None
                    if profile is None and t["status"] == "pending":
                        _mark(conn, coordinator, t["task_id"], status="failed", ended_at=utc_now(),
                              detail=f"its agent «{t['agent']}» is gone")  # fmt: skip
                        t["status"] = "failed"
                return profiles, local, _cloud(conn, coordinator)

            profiles, local, cloud_here = await self._db.acall(prepare)
            ends = {t["task_id"]: asyncio.Event() for t in rows}
            for t in rows:
                if t["status"] not in ("pending", "running", "checking"):
                    ends[t["task_id"]].set()
            pending = {t["task_id"]: t for t in rows if t["status"] == "pending"}
            ready = order([{**t, "dependencies": [d for d in t["dependencies"] if d in pending]}
                           for t in pending.values()], lambda t: local[t["task_id"]])  # fmt: skip
            under_way = [t for t in rows if t["status"] in ("running", "checking")]
            await self._told(coordinator)
            await self._go_through(coordinator, [pending[t["task_id"]] for t in ready], under_way, profiles, local,
                                   ends, cancel)  # fmt: skip
            if self._chat.stopping:  # the hub stops again: the batch goes on after its next start
                return
            said, error = await self._board_of(coordinator, number, cloud_here)
            answered = await self._db.acall(lambda conn: _answer_batch_call(conn, coordinator, said, error))
            if answered is not None:
                await self._bus.publish("chat.message", {"conversation_id": coordinator,
                                                         "message": answered.to_json()})  # fmt: skip
            if cancel.is_set():
                await self._finish(coordinator, Outcome(status="cancelled"))
            else:
                await self._launch(coordinator, resume=True, text=BATCH_RESUMED)
        except AgentError:  # the coordinator's chat was deleted meanwhile, and its run with it
            log.info("coordinator %s is gone: its batch %s ended with nobody to tell", coordinator, number)
        finally:
            self._resumed.pop(coordinator, None)

    async def task_diff(self, coordinator: str, args: dict[str, Any], cancel: asyncio.Event | None = None
                        ) -> tuple[str, bool]:  # fmt: skip
        """A coordinator's task_diff (61.1): what a task of its batches changed in its copies, whole - per project,
        or one file's - in pieces of DIFF_PIECE characters; a cloud coordinator is not shown a local-only project's
        (D10), nor anyone a file of secrets."""
        task_id = str(args.get("task_id") or "").strip()
        path = str(args.get("path") or "").replace("\\", "/").strip().strip("/")
        start = args.get("start_char") or 0
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            return "`start_char`: a whole number from 0", True

        def read(conn):
            tasks = {t["task_id"]: t for t in list_tasks(conn, coordinator)}
            task = tasks.get(task_id)
            projects = _attached(conn, task["run_id"]) if task and task["run_id"] and task["sandbox"] else []
            return list(tasks), task, projects, _cloud(conn, coordinator)

        names, task, projects, cloud = await self._db.acall(read)
        if task is None:
            return f"no task {task_id!r} in your batches; yours: {', '.join(names) or 'none'}", True
        folder = self._chat.sandboxes
        projects = [p for p in projects if sandbox.is_git(p)]
        if folder is None or not projects:
            return f"task {task_id!r} wrote into no copy of a project: there is nothing of it to show", True

        def whole() -> tuple[str, list[str]]:
            pieces, seen = [], []
            for project in projects:
                found = sandbox.changes(folder, task["run_id"], project)
                paths = [f["path"] for f in found["files"]]
                seen += paths
                if not paths or (path and path not in paths):
                    continue
                if cloud and not project.cloud_ok:
                    pieces.append(f"=== «{project.name}» ===\n" + LOCAL_ONLY.format(name=project.name))
                    continue
                parts = [safe_part(p, part) for p, part in diff_files(found["diff"], paths) if not path or p == path]
                pieces.append(f"=== «{project.name}» ===\n" + "".join(parts))
            return "\n".join(pieces), seen

        text, seen = await asyncio.to_thread(whole)
        if not text:
            if path and seen:
                return f"task {task_id!r} did not change {path!r}; it changed: {', '.join(seen)}", True
            return (f"task {task_id!r} has no changes waiting in its copies: it made none, or the user applied them "
                    "to the project or threw them away"), False  # fmt: skip
        if start >= len(text):
            return f"`start_char` {start} is past the end: the diff has {len(text)} characters", True
        piece = text[start : start + DIFF_PIECE]
        end = start + len(piece)
        more = f"; cut here - ask again with start_char={end}" if end < len(text) else ""
        return f"Task {task_id}: its changes, characters {start}-{end} of {len(text)}{more}.\n{piece}", False

    async def task_folder(self, coordinator: str, task_id: str, project: str = "") -> tuple[Path, Any]:
        """Where a task of the coordinator's batches has its files (61.7): its copy of the project, or the project
        itself when it has no copy (it did not write, or the copy went onto the project); with the project. `project`
        - which of the task's, when it has several. SnapshotError (for the model) - no such task, no project, or a
        local-only one for a cloud coordinator (D10)."""

        def read(conn):
            tasks = {t["task_id"]: t for t in list_tasks(conn, coordinator)}
            task = tasks.get(task_id)
            projects = _attached(conn, task["run_id"]) if task and task["run_id"] else []
            return list(tasks), task, projects, _cloud(conn, coordinator)

        names, task, projects, cloud = await self._db.acall(read)
        if task is None:
            raise SnapshotError(f"no task {task_id!r} in your batches; yours: {', '.join(names) or 'none'}")
        if not projects:
            raise SnapshotError(f"task {task_id!r} worked in no project: it has no files to show")
        wanted = project.strip().casefold()
        found = [p for p in projects if p.name.casefold() == wanted] if wanted else projects[:1]
        if not found or (len(projects) > 1 and not wanted):
            listed = ", ".join(p.name for p in projects)
            raise SnapshotError(f"`project`: which of task {task_id!r}'s projects - {listed}")
        chosen = found[0]
        if cloud and not chosen.cloud_ok:
            raise SnapshotError(LOCAL_PAGES.format(name=chosen.name))
        copy = sandbox.place(self._chat.sandboxes, task["run_id"], chosen) if self._chat.sandboxes else None
        return (copy if task["sandbox"] and copy is not None and copy.is_dir() else Path(chosen.path)), chosen

    async def _batch_task(self, parent_id: str, task: dict[str, Any], profile: dict[str, Any] | None,
                          ends: dict[str, asyncio.Event], before: str | None, cloud: asyncio.Semaphore | None,
                          cancel: asyncio.Event, *, resumed: bool = False) -> None:  # fmt: skip
        """One task of a batch: waits for its dependencies (and a local one for the local task before it), then runs
        as a sub-run of the coordinator; its row says how it went (`_finish` writes a run's end). `resumed`: it ran
        or was being checked when the hub restarted (61.5)."""
        task_id = task["task_id"]
        child_id = task["run_id"] if resumed else None
        try:
            if resumed:
                await self._resumed_task(parent_id, task, cancel)
                return
            assert profile is not None
            for waited in [*task["dependencies"], *([before] if before else [])]:
                await _either(ends[waited], cancel)
            if cancel.is_set():
                await self._db.acall(lambda conn: _mark(conn, parent_id, task_id, status="cancelled",
                                                        ended_at=utc_now()))  # fmt: skip
                await self._told(parent_id)
                return

            def start(conn) -> str | None:
                rows = {t["task_id"]: t for t in list_tasks(conn, parent_id)}
                missed = [f"{d} {MISSED.get(rows[d]['status'], rows[d]['status'])}" for d in task["dependencies"]
                          if rows[d]["status"] != "done"]  # fmt: skip
                if missed:
                    _mark(conn, parent_id, task_id, status="skipped", detail="; ".join(missed), ended_at=utc_now())
                    return None
                model = store.get_model(conn, profile["model_id"])
                cloudy = model is not None and model["locality"] == "cloud"  # not told a local-only one's (D10)
                found = []
                for d in task["dependencies"]:
                    names = local_names(conn, rows[d]["run_id"]) if cloudy else []
                    said = LOCAL_ANSWER.format(names=", ".join(names)) if names else rows[d]["answer"] or "(no answer)"
                    found.append(f"[{d}] «{rows[d]['agent']}»:\n{said}")
                prompt = task["prompt"] + ("\n\nWhat the tasks this one waited for found:\n\n" + "\n\n".join(found)
                                           if found else "")  # fmt: skip
                if task.get("files"):  # its own files (61.2)
                    prompt += FILES_NOTE + ", ".join(f"«{f['project']}: {f['path']}»" for f in task["files"])
                if task["criteria"]:  # it works knowing how it is checked (16.3)
                    prompt += DONE_WHEN + goals.criteria_text({"criteria": task["criteria"]})
                try:
                    run_id = create_run(conn, profile, prompt, profile["project_ids"], parent_run=parent_id)
                except (AgentError, ProjectError) as exc:
                    _mark(conn, parent_id, task_id, status="failed", detail=str(exc), ended_at=utc_now())
                    return None
                if task.get("files"):  # in its context, and nothing else of the projects: the window is the work's
                    store.set_context_settings(conn, run_id, map=False, live=False, pinned=task["files"])
                writes = profile["mode"] != "read_only" and bool(profile["project_ids"])
                sees = bool(profile["project_ids"]) and any(rows[d]["sandbox"] for d in task["dependencies"])
                if writes or sees:  # a copy of its own: to write into, or to read what those it waited for left
                    store.set_context_settings(conn, run_id, mode="sandbox")
                if sees and not writes:  # it only reads: nothing that writes or runs, whatever its profile has
                    tools = json.loads(conn.execute("SELECT tools FROM conversations WHERE id = ?",
                                                    (run_id,)).fetchone()[0] or "[]")  # fmt: skip
                    kept = [t for t in tools if approvals.kind(t["name"]) not in ("write", "command")]
                    conn.execute("UPDATE conversations SET tools = ? WHERE id = ?", (json.dumps(kept), run_id))
                _mark(conn, parent_id, task_id, status="running", run_id=run_id, started_at=utc_now())
                return run_id

            if cloud is not None:
                await cloud.acquire()
            try:
                child_id = await self._db.acall(start)
                await self._told(parent_id)
                if child_id is None:
                    return
                self._ended[child_id] = asyncio.Event()
                try:
                    clashed = await self._seed(parent_id, task, child_id)
                except sandbox.SandboxError as exc:
                    why = f"its sandbox could not start from what the tasks it waited for left: {exc}"
                    await self._finish(child_id, Outcome(status="error", detail=why))
                    return
                if clashed:  # it is told before it begins
                    await self._db.acall(lambda conn: conn.execute(
                        "UPDATE agent_runs SET task = task || ? WHERE conversation_id = ?",
                        (CONFLICTED + ", ".join(clashed), child_id)))  # fmt: skip
                if allowed := task_checks(task):  # allowed with the batch: run in its copy unasked (61.4)
                    self._chat.checks[child_id] = allowed
                bases = await self._bases(child_id) if task["criteria"] else {}
                await self._rounds(parent_id, task, child_id, cancel, bases, utc_now(), first=1)
            finally:
                if cloud is not None:
                    cloud.release()
        finally:
            if child_id is not None:
                self._ended.pop(child_id, None)
                self._chat.checks.pop(child_id, None)
            ends[task_id].set()

    async def _resumed_task(self, parent_id: str, task: dict[str, Any], cancel: asyncio.Event) -> None:
        """A task running or being checked when the hub restarted (61.5): its run goes on by itself (`start`), or it
        is checked again; then round after round as before. Its check diffs from where its copy began."""
        child_id = task["run_id"]
        self._ended.setdefault(child_id, asyncio.Event())
        if allowed := task_checks(task):  # the batch's checks were allowed before the restart
            self._chat.checks[child_id] = allowed
        bases = await self._copy_bases(child_id) if task["criteria"] else {}
        checking = task["status"] == "checking"
        done_rounds = task["rounds"] or 0  # checks made: one is under way when it is being checked
        first = min(max(done_rounds, 1) if checking else done_rounds + 1, ROUNDS)
        await self._rounds(parent_id, task, child_id, cancel, bases, task["started_at"] or utc_now(), first=first,
                           under_way=True, ended=checking)  # fmt: skip

    async def _rounds(self, parent_id: str, task: dict[str, Any], child_id: str, cancel: asyncio.Event,
                      bases: dict[str, str], since: str, *, first: int, under_way: bool = False,
                      ended: bool = False) -> None:  # fmt: skip
        """A task's run, then its check, round after round from `first` (16.3; one round without criteria);
        `under_way` - its run goes on already, `ended` - it ended and waits for its check (61.5)."""
        task_id = task["task_id"]
        note = None
        for round_no in range(first, ROUNDS + 1):
            its_end = self._ended[child_id]
            if not under_way:
                await self._launch(child_id, resume=False, text=note)
            if not ended:
                await _either(its_end, cancel)
                if not its_end.is_set():  # the coordinator was stopped: so is this task
                    await self.cancel(child_id)
                    await its_end.wait()
                    return
            under_way = ended = False
            if not task["criteria"]:
                return
            note = await self._check(parent_id, task, child_id, round_no, bases, since)
            if note is None:
                return
            if cancel.is_set():
                await self._db.acall(lambda conn: _mark(conn, parent_id, task_id, status="cancelled",
                                                        ended_at=utc_now()))  # fmt: skip
                await self._told(parent_id)
                return
            self._ended[child_id] = asyncio.Event()

    async def _allow_checks(self, parent_id: str, tasks: list[dict[str, Any]], mode: str,
                            cancel: asyncio.Event) -> str | None:  # fmt: skip
        """The check commands of the batch are the coordinator model's: the user allows them, one card per project
        (a goal's are allowed by setting it - D44); a «Доверенный» coordinator does not ask for those its rights let
        through (D17), after reading the web too (2026-11-18). None - allowed; else why not."""
        by_project: dict[str, list[str]] = {}
        for task in tasks:
            for c in task["criteria"] or []:
                if c["kind"] == "command":
                    by_project.setdefault(c["project_id"], [])
                    if c["command"] not in by_project[c["project_id"]]:
                        by_project[c["project_id"]].append(c["command"])
        if not by_project:
            return None
        for project_id, commands in by_project.items():
            project = await self._db.acall(lambda conn, p=project_id: get_project(conn, p))
            if mode == "trusted" and all(
                    trusted_skips("run_command", {"command": c}, project.root) for c in commands):  # fmt: skip
                continue
            asked = {
                "project": project.name,
                "command": "\n".join(commands),
                "purpose": "the checks of the coordinator's tasks: each runs in a task's copy when it ends, and the "
                "task's agent may run it there itself",
            }
            allowed, why = await self._chat._ask_user(parent_id, "run_command", asked, project, cancel, cover=False)
            if not allowed:
                return why or "not allowed"
        return None

    async def _copy_bases(self, run_id: str) -> dict[str, str]:
        """Where a task's copies began, as git trees (61.5): after a restart its check's diff runs from there."""
        folder = self._chat.sandboxes
        if folder is None:
            return {}
        projects = await self._db.acall(lambda conn: _attached(conn, run_id))

        def read() -> dict[str, str]:
            out = {}
            for project in (p for p in projects if sandbox.is_git(p)):
                try:
                    tree = sandbox.base_tree(folder, run_id, project)
                except sandbox.SandboxError as exc:
                    log.warning("task run %s: no starting point for %s: %s", run_id, project.name, exc)
                    continue
                if tree:
                    out[project.id] = tree
            return out

        return await asyncio.to_thread(read)

    async def _bases(self, run_id: str) -> dict[str, str]:
        """Where each git project's files stand as the task begins (in its copy): the judge's diff runs from there."""
        out = {}
        for project in await self._chat.tool_projects(run_id):
            if judge.is_git(project.root):
                try:
                    out[project.id] = await asyncio.to_thread(judge.snapshot, project.root)
                except judge.JudgeError as exc:
                    log.warning("task run %s: no starting point for %s: %s", run_id, project.name, exc)
        return out

    async def _check(self, parent_id: str, task: dict[str, Any], run_id: str, round_no: int, bases: dict[str, str],
                     since: str) -> str | None:  # fmt: skip
        """A task's check after its round (16.3): the goal's two contours - the criteria's commands in the task's
        copy, then the hub's look and the judge. Passed - the task is done; short with rounds left - the worker's
        word for the next one; short after the last, or a check nobody can make - a person is needed (None)."""
        task_id = task["task_id"]
        await self._db.acall(lambda conn: _mark(conn, parent_id, task_id, status="checking", rounds=round_no))
        await self._told(parent_id)
        goal = {
            "id": f"task:{parent_id}:{task_id}",
            "conversation_id": run_id,
            "objective": task["prompt"],
            "criteria": task["criteria"],
            "constraints": [],
            "judge_model_id": None,
            "created_at": since,
        }
        results = await self.goals.run_checks(goal)
        found = await self.goals.contour(goal, results, bases=bases)
        verdict, feedback = found.get("verdict"), found.get("feedback") or None
        missing = [line[2:] for line in goals.missing_text(found["results"], found.get("extra")).splitlines()
                   if line.startswith("- ")]  # fmt: skip
        if verdict == "ACHIEVED":
            fields = {"status": "done", "detail": found.get("detail")}
        elif found.get("blocked"):
            fields = {"status": "escalated", "detail": found["blocked"]}
        elif round_no >= ROUNDS:
            short = "; ".join(missing) or verdict or "the check did not pass"
            fields = {"status": "escalated", "detail": f"not passed after {ROUNDS} rounds: {short}"[:1000]}
        else:
            await self._db.acall(lambda conn: _mark(conn, parent_id, task_id, status="running", verdict=verdict,
                                                    feedback=feedback))  # fmt: skip
            await self._told(parent_id)
            return round_note(task, found, round_no + 1)
        await self._db.acall(lambda conn: _mark(conn, parent_id, task_id, verdict=verdict, feedback=feedback,
                                                ended_at=utc_now(), **fields))  # fmt: skip
        await self._told(parent_id)
        return None

    async def _told(self, coordinator: str) -> None:
        """The window hears that the coordinator's board changed (16.4): it reads the board again."""
        await self._bus.publish("run.tasks", {"run_id": coordinator}, persist=False)

    async def _seed(self, parent_id: str, task: dict[str, Any], run_id: str) -> list[str]:
        """A sandboxed task's copies of its projects start from what the tasks it waited for left there (16.2), not
        from the project's last commit; the files where two of them changed the same place, «project: path»."""
        folder = self._chat.sandboxes

        def read(conn):
            rows = {t["task_id"]: t for t in list_tasks(conn, parent_id)}
            waited = [rows[d]["run_id"] for d in task["dependencies"] if rows[d]["sandbox"] and rows[d]["run_id"]]
            mine = store.context_settings(conn, run_id)["mode"] == "sandbox"
            return (_attached(conn, run_id) if mine else []), waited

        projects, waited = await self._db.acall(read)
        if folder is None or not projects or not waited:
            return []

        def seed() -> list[str]:
            clashed = []
            for project in projects:
                if not sandbox.is_git(project):
                    continue
                left = [c for c in (sandbox.snapshot(folder, w, project) for w in waited) if c]
                if left:
                    start, conflicts = sandbox.start_from(project, left)
                    sandbox.ensure(folder, run_id, project, start=start)
                    clashed += [f"«{project.name}»: {name}" for name in conflicts]
            return clashed

        return await asyncio.to_thread(seed)

    async def _sandboxed_tasks(self, coordinator: str, batch: int | None = None) -> list[tuple[dict[str, Any], list]]:
        """The coordinator's tasks that wrote into sandboxes, in the order they ran, with their git projects."""

        def read(conn):
            tasks = applying_order(list_tasks(conn, coordinator, batch))
            return [(t, _attached(conn, t["run_id"])) for t in tasks if t["sandbox"] and t["run_id"]]

        found = await self._db.acall(read)
        return [(t, [p for p in projects if sandbox.is_git(p)]) for t, projects in found]

    async def task_changes(self, coordinator: str, batch: int | None = None) -> list[dict[str, Any]]:
        """What each of the coordinator's tasks changed in its sandboxes and has not gone onto the projects yet: the
        files and the diff per project (sandbox.changes); tasks with nothing there are left out."""
        folder = self._chat.sandboxes
        if folder is None:
            return []
        out = []
        for task, projects in await self._sandboxed_tasks(coordinator, batch):
            found = await asyncio.to_thread(
                lambda task=task, projects=projects: [
                    sandbox.changes(folder, task["run_id"], p) | {"cloud_ok": p.cloud_ok} for p in projects
                ]
            )
            found = [p for p in found if p["files"]]
            if found:
                out.append({"task_id": task["task_id"], "agent": task["agent"], "status": task["status"],
                            "run_id": task["run_id"], "projects": found})  # fmt: skip
        return out

    def busy_tasks(self, coordinator: str, tasks: list[dict[str, Any]]) -> bool:
        return any(r in self._chat.busy for r in [coordinator, *(t["run_id"] for t in tasks if t["run_id"])])

    async def apply_tasks(self, coordinator: str, files: dict[str, dict[str, list[str]]] | None = None,
                          hunks: dict[str, dict[str, dict[str, list[int]]]] | None = None) -> dict:  # fmt: skip
        """«Применить всё» (16.2): every done task's sandboxes onto the projects, in the order the tasks ran - each
        task's diff begins where the tasks before it ended, so one after another they do not clash. A task that did
        not end done keeps its sandbox (`kept`). Only the user does this (the window). With `files` («Применить
        выбранные», 17.1, D51): per task and project, only the files ticked - all checked before anything is
        written; what is not ticked stays in the tasks' sandboxes. With `hunks` (17.4): of a file, only those hunks.
        A task left out of `files` is not applied even when `hunks` names it."""
        folder = self._chat.sandboxes
        sandboxed = await self._sandboxed_tasks(coordinator)
        if files is not None or hunks:
            await asyncio.to_thread(_check_ticked, folder, sandboxed, files or {}, hunks or {})
        applied, kept = [], []
        for task, projects in sandboxed:
            if task["status"] != "done":
                kept.append(task["task_id"])
                continue
            for project in projects:
                only = None if files is None else files.get(task["task_id"], {}).get(project.id)
                if files is not None and only is None:
                    continue  # nothing of it ticked
                parts = (hunks or {}).get(task["task_id"], {}).get(project.id)
                done = await asyncio.to_thread(sandbox.apply, folder, task["run_id"], project, only, parts)
                if done["applied"]:
                    applied.append({"task_id": task["task_id"], "agent": task["agent"], "project_id": project.id,
                                    "project": project.name, "files": done["applied"],
                                    "conflicts": done["conflicts"], "kept": done["kept"]})  # fmt: skip
                    await self._bus.publish("sandbox.applied", {"conversation_id": coordinator,
                                                                "task_id": task["task_id"], "project_id": project.id,
                                                                **done}, project_id=project.id)  # fmt: skip
        return {"applied": applied, "kept": kept}

    async def discard_tasks(self, coordinator: str) -> dict[str, Any]:
        """«Отбросить всё» (16.2): every task's sandboxes go - worktrees, branches, refs; the projects stay as they
        are."""
        folder = self._chat.sandboxes
        gone = []
        for task, projects in await self._sandboxed_tasks(coordinator):
            had = [p for p in projects if sandbox.place(folder, task["run_id"], p).exists()]
            for project in had:
                await asyncio.to_thread(sandbox.discard, folder, task["run_id"], project)
            if had:
                gone.append(task["task_id"])
        if gone:
            await self._bus.publish("sandbox.discarded", {"conversation_id": coordinator, "task_ids": gone})
        return {"discarded": gone}

    async def cancel(self, run_id: str) -> bool:
        resumed = self._resumed.get(run_id)
        if resumed is not None:  # a coordinator whose batch goes on after a restart (61.5): stopped with it
            resumed.set()
            return True
        queued = self._waiting.pop(run_id, None)
        if queued is not None:  # not under way yet - waiting for the GPU, or getting ready: it never starts
            queued.cancel()
            await self._finish(run_id, Outcome(status="cancelled"))
            return True
        return self._chat.cancel(run_id)

    async def _launch(self, run_id: str, *, resume: bool, text: str | None = None) -> None:
        """The run starts (or goes on: `text` - the hub's word for its next round, 16.3)."""
        run = await self._db.acall(lambda conn: get_run(conn, run_id))
        model = await self._db.acall(lambda conn: store.get_model(conn, run["model_id"]))
        local = model is not None and model["locality"] == "local"
        task = asyncio.create_task(self._go(run, local=local, resume=resume, text=text))
        self._waiting[run_id] = task  # till its chat run is under way, a stop cancels this task
        task.add_done_callback(log_task_exception)

    async def _go(self, run: dict[str, Any], *, local: bool, resume: bool, text: str | None = None) -> None:
        """The run begins (`_begin`). Whatever fails before its chat run is under way ends it here, so the GPU it
        took goes on to the next run (`_finish`); a stop till then cancels this task (`cancel`)."""
        run_id = run["conversation_id"]
        try:
            await self._begin(run, local=local, resume=resume, text=text)
        except GatewayError as exc:
            await self._finish(run_id, Outcome(status="error", detail=str(exc)))
        except Exception:
            log.exception("run %s did not start", run_id)
            await self._finish(run_id, Outcome(status="error", detail="internal error"))

    async def _begin(self, run: dict[str, Any], *, local: bool, resume: bool, text: str | None) -> None:
        """Its turn for the GPU (a local run), then its chat run - under way."""
        run_id = run["conversation_id"]
        await self._status(run_id, "queued")
        if local:
            await self._gpu.acquire()  # one local run at a time: the GPU holds one model (D16)
            self._held.add(run_id)
        auto = run.get("kind") == "autonomous"
        if auto and (rules := autonomous.conditions(run.get("done_when"))):  # refused at the call, as a goal's (25.1)
            self._chat.guards[run_id] = guard.TurnGuard(rules)
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
        text = (text or RESUMED) if resume else (text or run["task"])
        await self._chat.start(run_id, model_id, text, limits=limits,
                               on_end=lambda outcome: self._finish(run_id, outcome))  # fmt: skip
        self._waiting.pop(run_id, None)  # under way: a stop goes to its chat run now

    async def _finish(self, run_id: str, outcome: Outcome) -> None:
        """How the run ended, written and told (`_record_end`). Whoever waits for it (`_ended`: a coordinator) hears
        of it whatever happens there - a run whose chat was deleted has no row left to write into."""
        self._waiting.pop(run_id, None)  # it ended before it was under way: nothing to cancel now
        try:
            await self._record_end(run_id, outcome)
        finally:
            ended = self._ended.get(run_id)
            if ended is not None:
                ended.set()

    async def _record_end(self, run_id: str, outcome: Outcome) -> None:
        if run_id in self._held:
            self._held.discard(run_id)
            self._gpu.release()
        if self._chat.stopping:  # the hub stops, not the run: it goes on after the restart as it was left (start)
            return
        totals = outcome.totals
        status = outcome.status if outcome.status in ("done", "error", "cancelled") else "done"
        if self._chat.computer is not None:  # its hold on the desktop goes with it (D41)
            await self._chat.computer.release(run_id)
        self._chat.guards.pop(run_id, None)
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
            conn.execute(  # a batch's task: the board says how it ended (16.1); one with criteria - checked next (16.3)
                "UPDATE delegated_tasks SET status = CASE WHEN ? = 'done' AND criteria IS NOT NULL THEN 'checking'"
                " ELSE ? END, answer = ?, detail = ?, ended_at = ? WHERE run_id = ? AND status IN ('running',"
                " 'checking')",
                (ENDED[status], ENDED[status], _last_answer(conn, run_id) or None, outcome.detail or None, utc_now(),
                 run_id),
            )  # fmt: skip
            board_of = conn.execute("SELECT coordinator FROM delegated_tasks WHERE run_id = ?", (run_id,)).fetchone()
            try:
                run = get_run(conn, run_id)
            except AgentError:  # its chat was deleted, and the run with it: nothing to tell the window
                run = None
            return run, board_of[0] if board_of else None

        run, coordinator = await self._db.acall(write)
        if run is not None:
            await self._bus.publish("run.finished", {"run": run})
        if coordinator:
            await self._told(coordinator)

    async def _status(self, run_id: str, status: str) -> None:
        await self._db.acall(
            lambda conn: conn.execute("UPDATE agent_runs SET status = ? WHERE conversation_id = ?", (status, run_id))
        )
        await self._bus.publish("run.status", {"run_id": run_id, "status": status}, persist=False)
