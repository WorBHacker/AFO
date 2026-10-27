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
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from . import store
from .chat import CHAT_TOOLS, ChatService, Limits, Outcome
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database, utc_now
from .events import EventBus
from .gateway import GatewayError
from .projects import ProjectError, get_project

log = logging.getLogger("hub.agents")

MODES = ("read_only", "ask", "trusted")
STEPS = (1, 200)
MINUTES = (1, 24 * 60)
NAME_CHARS = 60
INSTRUCTIONS_CHARS = 8000
TOOL_NAMES = [t["name"] for t in CHAT_TOOLS]
RESUMED = "[LocalChat hub] The hub restarted while you worked on this task. Look at where you stopped and go on."
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

RUN_SELECT = (
    "SELECT r.*, EXISTS (SELECT 1 FROM approvals a WHERE a.conversation_id = r.conversation_id"
    " AND a.status = 'pending') AS waiting FROM agent_runs r"
)


def _run(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    run["waiting"] = bool(run["waiting"])
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


def create_run(conn: sqlite3.Connection, profile: dict[str, Any], task: str, project_ids: list[str]) -> str:
    """The run's conversation, set up as the profile says: projects, rights, its fixed tool list, and its row."""
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
    wanted = profile["tools"] or TOOL_NAMES
    tools = [t for t in CHAT_TOOLS if t["name"] in wanted]
    conn.execute("UPDATE conversations SET tools = ? WHERE id = ?", (json.dumps(tools), conversation_id))
    conn.execute(
        "INSERT INTO agent_runs (conversation_id, profile_id, profile_version, profile_name, instructions, model_id,"
        " max_steps, max_minutes, budget_usd, task) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, profile["id"], profile["version"], profile["name"], profile["instructions"],
         profile["model_id"], profile["max_steps"], profile["max_minutes"], profile["budget_usd"], task),
    )  # fmt: skip
    return conversation_id


class AgentRunner:
    """Starts runs, keeps the GPU queue (local models one at a time), records how they end, resumes them."""

    def __init__(self, db: Database, bus: EventBus, chat: ChatService):
        self._db = db
        self._bus = bus
        self._chat = chat
        self._gpu = asyncio.Semaphore(1)
        self._held: set[str] = set()  # runs holding the GPU now
        self._waiting: dict[str, asyncio.Task] = {}  # runs queued for the GPU

    async def start(self) -> list[str]:
        """Runs the hub was running (or had queued) when it stopped go on now."""
        unfinished = await self._db.acall(
            lambda conn: [r["conversation_id"] for r in conn.execute(
                "SELECT conversation_id FROM agent_runs WHERE status IN ('queued', 'running') ORDER BY created_at"
            ).fetchall()]
        )  # fmt: skip
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
        limits = Limits(max_steps=run["max_steps"], deadline=time.monotonic() + run["max_minutes"] * 60,
                        budget_usd=run["budget_usd"])  # fmt: skip
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE agent_runs SET status = 'running', started_at = COALESCE(started_at, ?) WHERE conversation_id = ?",
            (utc_now(), run_id)))  # fmt: skip
        current = await self._db.acall(lambda conn: get_run(conn, run_id))
        await self._bus.publish("run.started", {"run": current, "resumed": resume})
        text = RESUMED if resume else run["task"]
        try:
            await self._chat.start(run_id, run["model_id"], text, limits=limits,
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

        def write(conn):
            conn.execute(
                "UPDATE agent_runs SET status = ?, detail = ?, steps = steps + ?, cost_usd = cost_usd + ?,"
                " ended_at = ? WHERE conversation_id = ?",
                (status, outcome.detail or None, totals.steps if totals else 0, totals.cost if totals else 0.0,
                 utc_now(), run_id),
            )  # fmt: skip
            return get_run(conn, run_id)

        run = await self._db.acall(write)
        await self._bus.publish("run.finished", {"run": run})

    async def _status(self, run_id: str, status: str) -> None:
        await self._db.acall(
            lambda conn: conn.execute("UPDATE agent_runs SET status = ? WHERE conversation_id = ?", (status, run_id))
        )
        await self._bus.publish("run.status", {"run_id": run_id, "status": status}, persist=False)
