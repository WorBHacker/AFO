"""What the user does in VS Code (stage 6.6, D4, Q7): the file open in the editor and the commands run in its terminals,
sent by the LocalChat extension (vscode/). The hub - and the models of its chats - then see them as they see the hub's
own terminals.

The open file lives in memory only (it changes all the time; an event only for live listeners) and counts as current
for an hour. A finished command is an event (editor.command) of the project its folder is in: the feed shows it, and
the next message of a chat attached to that project hears of it - with the end of its output when it failed. Output
is cleaned of terminal escapes and masked (secrets), and only its last lines are kept.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .db import Database
from .events import EventBus
from .projects import list_projects
from .secret_filter import clean

FOCUS_FRESH_S = 3600  # an open file reported longer ago than this is not "open now" any more
OUTPUT_CHARS = 2000
OUTPUT_LINES = 25
COMMAND_CHARS = 500
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\r")


def plain_output(text: str) -> str:
    """The end of a command's output as text: escapes out, at most OUTPUT_LINES lines and OUTPUT_CHARS characters,
    secrets masked."""
    lines = [line.rstrip() for line in ESCAPES.sub("", text).split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    tail = "\n".join(lines[-OUTPUT_LINES:])
    return clean(tail[-OUTPUT_CHARS:])


def project_of(path: str, projects: list[Any]) -> tuple[Any | None, str]:
    """The project a file or folder is in (the deepest one) and the path inside it, with forward slashes; Windows
    paths compare without case."""
    norm = path.replace("/", "\\").rstrip("\\")
    best, best_root, rel = None, "", ""
    for project in projects:
        root = str(project.root).replace("/", "\\").rstrip("\\")
        inside = norm.lower() == root.lower() or norm.lower().startswith(root.lower() + "\\")
        if inside and len(root) > len(best_root):
            best, best_root, rel = project, root, norm[len(root) :].lstrip("\\").replace("\\", "/")
    return best, rel


class Editor:
    def __init__(self, db: Database, bus: EventBus, clock=time.time):
        self._db = db
        self._bus = bus
        self._clock = clock
        self._focus: dict[str, Any] | None = None

    async def focus(self, client: str, path: str, line: int) -> dict[str, Any]:
        found, rel = project_of(path, await self._db.acall(list_projects))
        self._focus = {"client": client, "path": path, "line": max(int(line), 1), "project_id": found.id if found
                       else None, "project": found.name if found else "", "rel": rel, "at": self._clock()}  # fmt: skip
        await self._bus.publish("editor.focus", self.shown(), persist=False)
        return self.shown()

    def shown(self) -> dict[str, Any]:
        return {k: v for k, v in (self._focus or {}).items() if k != "at"}

    def open_in(self, project_id: str) -> str:
        """'src/app.py:42' when VS Code has a file of this project open now, else ''."""
        f = self._focus
        if not f or f["project_id"] != project_id or self._clock() - f["at"] > FOCUS_FRESH_S:
            return ""
        return f"{f['rel']}:{f['line']}"

    async def command(self, client: str, *, terminal: str, cwd: str, command: str, exit_code: int | None,
                      output: str, duration_s: float | None) -> dict[str, Any]:  # fmt: skip
        found, _rel = project_of(cwd, await self._db.acall(list_projects)) if cwd else (None, "")
        payload = {
            "client": client,
            "terminal": terminal[:80],
            "cwd": cwd,
            "command": clean(" ".join(command.split())[:COMMAND_CHARS]),
            "exit_code": exit_code,
            "duration_s": round(duration_s, 1) if duration_s is not None else None,
            "output": plain_output(output),
            "project": found.name if found else "",
        }
        await self._bus.publish("editor.command", payload, project_id=found.id if found else None)
        return payload
