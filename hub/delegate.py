"""The coordinator's tools (stage 6, D20; stage 16, D50): delegate_task gives a part of an agent's task to another
agent of the hub; delegate_tasks gives a batch at once - independent tasks at the same time, a task that depends on
others after them. The runs are hub/agents.py's; this is the tools as a model sees them, and the batch's shape checked
before anything starts."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

MAX_TASKS = 8
TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

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
                    },
                    "required": ["task_id", "agent_profile", "prompt"],
                },
            },
        },
        "required": ["tasks"],
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
        tasks.append({"task_id": task_id, "agent_profile": agent, "prompt": prompt, "dependencies": deps})
    ids = {t["task_id"] for t in tasks}
    for task in tasks:
        for dep in task["dependencies"]:
            if dep not in ids:
                raise BatchError(f"task {task['task_id']!r} depends on {dep!r}, which is not in this batch")
    circle = _circle(tasks)
    if circle:
        raise BatchError(f"the tasks wait for each other in a circle: {', '.join(circle)}")
    return tasks


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
