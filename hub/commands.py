"""Commands the hub's models run (stage 5, D17): run_command, in a terminal of the hub the user sees in the app.

A command is asked in the approval queue every time, unless an answer "for this chat" or the project's rule (its
whitelist: "always in this project" on that exact command) covers it, or the chat is trusted (since 2026-10-28, the
user's word: a trusted chat runs commands without asking). Destructive commands (git push, rm -r...) are asked every
time, a trusted chat too; commands that reach the network (installs, downloads, D19) are never covered by an
answer or a rule. The command runs through the terminal manager -
a pseudo-console like any terminal the user opens, with its log - and the tool waits for it to end (up to a timeout),
then gives the model the exit code and the last lines. A command still running after the timeout (a server) keeps
running; the model reads it later with terminal_output.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .limits import limit
from .projects import Project, ProjectError, resolve_inside
from .secret_filter import clean
from .tools import TERMINAL_LINES, ToolError, plain_terminal_text

DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
POLL_S = 0.2
TITLE_CHARS = 48

RUN_COMMAND: dict[str, Any] = {
    "name": "run_command",
    "description": "Run a shell command (PowerShell) in a project's folder, in a terminal of the hub that the user "
    f"sees in the app. The user is asked first, unless the project allows this exact command. Waits up to timeout_s "
    f"seconds (default {DEFAULT_TIMEOUT_S}, at most {MAX_TIMEOUT_S}) and returns the exit code and the last "
    f"{TERMINAL_LINES} lines. A command still running then (a server) keeps running: read it later with "
    "terminal_output. Destructive commands and commands that use the network are asked every time.",
    "parameters": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "The project's name, as listed in the system prompt."},
            "command": {"type": "string", "description": "The command line, as typed in PowerShell."},
            "path": {"type": "string", "description": "A folder inside the project to run it in (default: its root)."},
            "timeout_s": {"type": "integer", "description": f"How long to wait for it (default {DEFAULT_TIMEOUT_S})."},
        },
        "required": ["project", "command"],
    },
}


def command_of(args: dict[str, Any]) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("`command` must be a command line")
    if "\n" in command.strip():
        raise ToolError("one command line at a time (join steps with ; if they belong together)")
    return command.strip()


async def run(terminals: Any, project: Project, args: dict[str, Any], *, by: str) -> str:
    """Runs the command and waits for it (or for the timeout); what the model reads."""
    command = command_of(args)
    if terminals is None:
        raise ToolError("terminals are not available")
    folder = args.get("path") or ""
    if not isinstance(folder, str):
        raise ToolError("`path` must be a folder inside the project")
    try:
        cwd = resolve_inside(project.root, folder) if folder else project.root
    except ProjectError as exc:
        raise ToolError(str(exc)) from exc
    timeout = args.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ToolError("`timeout_s` must be a whole number of seconds")
    timeout = max(1, min(timeout, MAX_TIMEOUT_S))
    title = "Модель: " + (command if len(command) <= TITLE_CHARS else command[: TITLE_CHARS - 1] + "…")
    info = await terminals.create(cwd=cwd, by=by, project_id=project.id, command=command, title=title)
    deadline = time.monotonic() + timeout
    while info["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(POLL_S)
        info = await terminals.get(info["id"])
    output = await terminals.output(info["id"], 256 * 1024)
    lines = plain_terminal_text(output.get("data", "")).rstrip("\n").splitlines()
    count = limit("terminal_lines")
    tail = "\n".join(lines[-count:])
    shown = f"last {min(len(lines), count)} of {len(lines)} lines" if lines else "no output"
    if info["status"] == "running":
        head = (f"still running after {timeout} s in terminal {info['id']} ({title}); it goes on - read it later "
                f"with terminal_output. {shown}:")  # fmt: skip
    else:
        head = f"exit code {info['exit_code']}; {shown}:"
    return clean(f"$ {command}\n{head}\n{tail}")
