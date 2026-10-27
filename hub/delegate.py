"""The coordinator's tool (stage 6, D20): delegate_task gives a part of an agent's task to another agent of the hub.
The run is hub/agents.py's; this is only the tool as a model sees it."""

from __future__ import annotations

from typing import Any

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
