"""The coordinator's tools (stage 6, D20; stage 16, D50): delegate_task gives a part of an agent's task to another
agent of the hub; delegate_tasks gives a batch at once - independent tasks at the same time, a task that depends on
others after them. The runs are hub/agents.py's; this is the tools as a model sees them, and the batch's shape checked
before anything starts."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from typing import Any

from . import store
from .claude_code import PROVIDER as CLAUDE_CODE

MAX_TASKS = 8
MODELS_SHOWN = 30  # the models a coordinator's role and its errors name (61.3): a hub may know hundreds
MAX_FILES = 3  # a task's own files (61.2): put before its agent, the rest of its window left for the work
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ROUNDS = 3  # a task with criteria: its work, then rounds after checks that found it short - in all (16.3)

DELEGATE: dict[str, Any] = {
    "name": "delegate_task",
    "description": "Give a part of your task to another agent of the hub and wait for its answer. The agent works "
    "on its own - with its own tools and rights, the user answering its approvals - and its final answer comes "
    "back to you as this tool's result. Name the agent as your role lists it; say the task fully, it does not see "
    "your conversation. One level only: an agent you give a task to cannot give tasks further.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "The agent's name."},
            "task": {"type": "string", "description": "What it should do, complete in itself."},
        },
        "required": ["agent", "task"],
    },
}

DELEGATE_MANY: dict[str, Any] = {
    "name": "delegate_tasks",
    "description": f"Give up to {MAX_TASKS} parts of your task to other agents of the hub at once and wait for all "
    "of them. Tasks that do not depend on each other run at the same time (agents on local models take the graphics "
    "card one after another); a task with `dependencies` starts when those are done and gets their answers. Each "
    "agent works on its own - its own tools and rights, the user answering its approvals - and does not see your "
    "conversation: say each task fully. You get back a board: each task's status and the agent's final answer; a "
    "task whose dependency failed is skipped. One level only: the agents cannot give tasks further.",
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": f"1 to {MAX_TASKS} tasks.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "A short name of the task in this batch: letters, digits, - or _.",
                        },
                        "agent_profile": {"type": "string", "description": "The agent's name, as your role lists it."},
                        "prompt": {"type": "string", "description": "What it should do, complete in itself."},
                        "dependencies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "task_ids of this batch that must be done first.",
                        },
                        "criteria": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "How the hub checks the task when the agent ends - the agent cannot "
                            "declare it done: {checks: true, project?} - the project's own checks the hub finds by "
                            "its files (tests, a linter, types); {command, project?, expected_exit_code?, path?} - a "
                            "command that only looks (tests, a build); both run in the task's copy of the project, and "
                            "its agent may run them there itself; or {rule} - in words, for the judge model. Not met: "
                            f"the agent works on with what the check found, up to {ROUNDS} rounds in all; then the "
                            "task needs a person. The user allows the commands.",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"Up to {MAX_FILES} files the task is about: the hub puts their text "
                            "before the agent and leaves out the map of the projects and their live state - a small "
                            "model's window stays for the work. A path in the agent's project, or «project: path» "
                            "when it has several.",
                        },
                        "model": {
                            "type": "string",
                            "description": "The model to run the task on, as your role lists the models - instead "
                            "of its agent's own; the agent's role, tools and rights stay.",
                        },
                    },
                    "required": ["task_id", "agent_profile", "prompt"],
                },
            },
        },
        "required": ["tasks"],
    },
}


DIFF_PIECE = 12_000  # characters of a task's changes per task_diff call (61.1)
TASK_DIFF: dict[str, Any] = {
    "name": "task_diff",
    "description": "The changes a task of your batches made in its copy of a project: the diff with the lines around "
    "each change - the board shows them only in short. A long one comes in pieces: ask again from `start_char`. "
    "None of it is in the project yet: only the user puts it there.",
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task, as your batch named it."},
            "path": {"type": "string", "description": "Only this file's changes, as the board names it."},
            "start_char": {"type": "integer", "description": "Where to go on from, when the last piece was cut."},
        },
        "required": ["task_id"],
    },
}


class BatchError(ValueError):
    """The batch cannot run as written; the message is for the model."""


def check_batch(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The batch's tasks as the hub runs them - task_id, agent_profile, prompt, dependencies - or BatchError: the
    count, the names, each task's agent and prompt, dependencies inside the batch and not in a circle."""
    given = args.get("tasks") if isinstance(args, dict) else None
    if not isinstance(given, list) or not 1 <= len(given) <= MAX_TASKS:
        raise BatchError(f"`tasks`: a list of 1 to {MAX_TASKS} tasks")
    tasks: list[dict[str, Any]] = []
    for item in given:
        item = item if isinstance(item, dict) else {}
        task_id = str(item.get("task_id") or "")
        if not TASK_ID.match(task_id):
            raise BatchError(f"task_id {task_id!r}: 1 to 40 letters, digits, - or _")
        agent, prompt = str(item.get("agent_profile") or "").strip(), str(item.get("prompt") or "").strip()
        if not agent or not prompt:
            raise BatchError(f"task {task_id!r} needs `agent_profile` and `prompt`")
        if any(t["task_id"] == task_id for t in tasks):
            raise BatchError(f"task_id {task_id!r} is used twice")
        deps = item.get("dependencies") or []
        deps = list(dict.fromkeys(str(d) for d in deps)) if isinstance(deps, list) else [str(deps)]
        criteria = item.get("criteria") or None  # checked against the agent's projects (hub/agents.py)
        files = item.get("files") or []  # found in the agent's projects (hub/agents.py)
        if (not isinstance(files, list) or len(files) > MAX_FILES
                or not all(isinstance(f, str) and f.strip() for f in files)):  # fmt: skip
            raise BatchError(f"task {task_id!r}: `files` - up to {MAX_FILES} paths")
        model = item.get("model") or ""  # found among the hub's models (hub/agents.py)
        if not isinstance(model, str):
            raise BatchError(f"task {task_id!r}: `model` - a model's name, as your role lists them")
        tasks.append({"task_id": task_id, "agent_profile": agent, "prompt": prompt, "dependencies": deps,
                      "criteria": criteria, "files": list(dict.fromkeys(f.strip() for f in files)),
                      "model": model.strip()})  # fmt: skip
    ids = {t["task_id"] for t in tasks}
    for task in tasks:
        for dep in task["dependencies"]:
            if dep not in ids:
                raise BatchError(f"task {task['task_id']!r} depends on {dep!r}, which is not in this batch")
    circle = _circle(tasks)
    if circle:
        raise BatchError(f"the tasks wait for each other in a circle: {', '.join(circle)}")
    return tasks


def task_models(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The models a task may be given (61.3): enabled, offered in the picker, not Claude Code (an agent of its own)."""
    return [m for m in store.list_models(conn) if not m.get("hidden") and m["provider"] != CLAUDE_CODE]


def models_text(models: list[dict[str, Any]]) -> str:
    """The models by their names as a task takes them, local or cloud - MODELS_SHOWN of them at most."""
    shown = ", ".join(f"{m['id']} ({m['locality']})" for m in models[:MODELS_SHOWN]) or "none"
    return shown + (f" and {len(models) - MODELS_SHOWN} more" if len(models) > MODELS_SHOWN else "")


def _circle(tasks: list[dict[str, Any]]) -> list[str]:
    """The tasks that can never start (each waits, at the end, for itself), in the batch's order; [] when none."""
    left = {t["task_id"]: set(t["dependencies"]) for t in tasks}
    while True:
        free = [task_id for task_id, deps in left.items() if not deps & left.keys()]
        if not free:
            return [t["task_id"] for t in tasks if t["task_id"] in left]
        for task_id in free:
            del left[task_id]


def order(tasks: list[dict[str, Any]], model_of: Callable[[dict[str, Any]], str | None]) -> list[dict[str, Any]]:
    """The batch in an order every task can start in (each after its dependencies): of the tasks ready, the one on
    the model just used comes first - local models take the GPU one after another, and llama-swap then loads each
    model once. `model_of`: a local task's model, None for a cloud one."""
    done: set[str] = set()
    out: list[dict[str, Any]] = []
    last: str | None = None
    while len(out) < len(tasks):
        ready = [t for t in tasks if t["task_id"] not in done and set(t["dependencies"]) <= done]
        same = [t for t in ready if last is not None and model_of(t) == last]
        chosen = (same or ready)[0]
        out.append(chosen)
        done.add(chosen["task_id"])
        last = model_of(chosen) or last
    return out
