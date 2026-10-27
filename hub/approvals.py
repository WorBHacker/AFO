"""The approval queue (D17, D18): an agent asks before it acts, and waits until the user answers.

Claude Code started by the hub asks through the hub's MCP tool approval_prompt (its --permission-prompt-tool); the
request lands here, in the same queue the hub's own agents will use (stage 5). The user answers in the app:
allow once, allow for this chat, or deny with a comment the agent reads. An answer "for this chat" covers later
requests of the same kind in that chat - the same tool, or for a command the same command - except destructive
commands (git push, reset --hard, rm -r...), which are asked every time (D17). Each request is a row in `approvals`
with who asked, what, and who answered how; nothing is forgotten.

Events: approval.requested {approval}, approval.decided {approval} - both in the feed. A request still open when
its run ends (or the hub stops) is closed as expired: nobody waits for that answer any more.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database, utc_now
from .events import EventBus

COMMAND_TOOLS = {"Bash", "PowerShell"}
WRITE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
NETWORK_TOOLS = {"WebFetch", "WebSearch"}
SCOPES = ("once", "chat")
PREVIEW_LINES = 400
PREVIEW_CHARS = 40_000
READ_MAX_BYTES = 2 * 1024 * 1024
DESTRUCTIVE = re.compile(
    r"\bgit\s+(push|reset\s+--hard|clean\s+-\w*f|branch\s+-D|checkout\s+--\s|restore\b|stash\s+(drop|clear))"
    r"|\brm\s+-\w*[rf]|\b(rmdir|rd)\s+/s|\bdel\s+/[sq]|\bRemove-Item\b.*-Recurse|\bformat\s+[a-z]:"
    r"|\bdrop\s+(table|database)\b|\bshutdown\b|\bmkfs\b",
    re.IGNORECASE,
)
DENIED = "the user denied this"
EXPIRED = "nobody answered: the run ended before the user did"


class ApprovalError(Exception):
    """The message is fit to show."""


@dataclass(frozen=True)
class Decision:
    allow: bool
    comment: str = ""


def kind(tool: str) -> str:
    """command, write, network or other - how the app shows the request."""
    if tool in COMMAND_TOOLS:
        return "command"
    if tool in WRITE_TOOLS:
        return "write"
    return "network" if tool in NETWORK_TOOLS else "other"


def destructive(tool: str, data: dict[str, Any]) -> bool:
    return tool in COMMAND_TOOLS and bool(DESTRUCTIVE.search(str(data.get("command", ""))))


def grant_key(tool: str, data: dict[str, Any]) -> str | None:
    """What an answer "for this chat" covers; None when nothing may be covered so (a destructive command)."""
    if destructive(tool, data):
        return None
    if tool in COMMAND_TOOLS:
        return f"{tool}:{' '.join(str(data.get('command', '')).split())}"
    return tool


def preview(tool: str, data: dict[str, Any], root: Path | None) -> dict[str, str]:
    """What the user should see to decide: a command, a diff of the file as it would become, a URL, or the input."""
    if tool in COMMAND_TOOLS:
        return {"kind": "command", "text": str(data.get("command", "")), "note": str(data.get("description", ""))}
    if tool in ("Edit", "MultiEdit", "Write"):
        return {"kind": "diff", "text": _diff(tool, data, root), "note": str(data.get("file_path", ""))}
    if tool == "WebFetch":
        return {"kind": "text", "text": str(data.get("url", "")), "note": str(data.get("prompt", ""))}
    if tool == "WebSearch":
        return {"kind": "text", "text": str(data.get("query", "")), "note": ""}
    return {"kind": "text", "text": _cut(json.dumps(data, ensure_ascii=False, indent=2)), "note": ""}


def _diff(tool: str, data: dict[str, Any], root: Path | None) -> str:
    name = str(data.get("file_path", ""))
    path = Path(name)
    if not path.is_absolute() and root is not None:
        path = root / path
    before = _read(path)
    if tool == "Write":
        after = str(data.get("content", ""))
    else:
        edits = data.get("edits") if tool == "MultiEdit" else [data]
        after = before
        for edit in edits or []:
            old, new = str(edit.get("old_string", "")), str(edit.get("new_string", ""))
            if after is not None and old and old in after:
                after = after.replace(old, new) if edit.get("replace_all") else after.replace(old, new, 1)
            else:  # the file is gone, or the text is not in it: show the edit itself
                before, after = (before or "") + old, (after if after is not None else "") + new
    shown = path.relative_to(root).as_posix() if root is not None and path.is_relative_to(root) else name
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=f"a/{shown}" if before is not None else "/dev/null",
        tofile=f"b/{shown}",
    )
    return _cut("".join(line if line.endswith("\n") else line + "\n" for line in lines))


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > READ_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _cut(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) > PREVIEW_LINES:
        text = "".join(lines[:PREVIEW_LINES]) + f"… ещё {len(lines) - PREVIEW_LINES} строк\n"
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "\n… (обрезано)\n"


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["input"])
    return dict(row) | {"input": data, "kind": kind(row["tool"]), "destructive": destructive(row["tool"], data)}


class ApprovalQueue:
    def __init__(self, db: Database, bus: EventBus):
        self._db = db
        self._bus = bus
        self._waiting: dict[str, asyncio.Future[Decision]] = {}

    async def start(self) -> int:
        """Requests left open by a hub that stopped: nobody waits for them now."""
        return await self._db.acall(
            lambda conn: (
                conn.execute(
                    "UPDATE approvals SET status = 'expired', decided_at = ?, decided_by = 'hub'"
                    " WHERE status = 'pending'",
                    (utc_now(),),
                ).rowcount
            )
        )

    async def ask(
        self,
        tool: str,
        data: dict[str, Any],
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
        client_id: str | None = None,
        root: Path | None = None,
    ) -> Decision:
        """Queues the request and waits for the user's answer - or answers at once when an earlier answer "for this
        chat" covers it."""
        key = grant_key(tool, data)
        approval_id = uuid.uuid4().hex[:12]
        shown = await asyncio.to_thread(preview, tool, data, root)

        def write(conn: sqlite3.Connection) -> sqlite3.Row:
            covered = (
                bool(conversation_id and key)
                and conn.execute(
                    "SELECT 1 FROM approvals WHERE conversation_id = ? AND grant_key = ? AND status = 'allowed'"
                    " AND scope = 'chat'",
                    (conversation_id, key),
                ).fetchone()
            )
            conn.execute(
                "INSERT INTO approvals (id, conversation_id, project_id, client_id, tool, input, grant_key, status,"
                " scope, decided_at, decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (approval_id, conversation_id, project_id, client_id, tool, json.dumps(data, ensure_ascii=False), key,
                 *(("allowed", "auto", utc_now(), "hub") if covered else ("pending", None, None, None))),
            )  # fmt: skip
            return conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()

        row = _row(await self._db.acall(write)) | {"preview": shown}
        if row["status"] == "allowed":
            await self._bus.publish("approval.decided", {"approval": row}, project_id=project_id, persist=False)
            return Decision(True)
        future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._waiting[approval_id] = future
        await self._bus.publish("approval.requested", {"approval": row}, project_id=project_id)
        try:
            return await future
        except asyncio.CancelledError:  # whoever asked went away (its request was cut): close the row
            with contextlib.suppress(ApprovalError):
                await self._close(approval_id, "expired", None, EXPIRED, "hub")
            raise
        finally:
            self._waiting.pop(approval_id, None)

    async def decide(self, approval_id: str, *, allow: bool, scope: str = "once", comment: str = "", by: str) -> dict:
        if allow and scope not in SCOPES:
            raise ApprovalError(f"unknown scope {scope!r}: {', '.join(SCOPES)}")
        return await self._close(approval_id, "allowed" if allow else "denied", scope if allow else None,
                                 comment.strip(), by)  # fmt: skip

    async def expire(self, conversation_id: str) -> int:
        """The run of this chat ended: its open requests close, and whoever still waits gets a denial."""
        rows = await self._db.acall(
            lambda conn: conn.execute(
                "SELECT id FROM approvals WHERE conversation_id = ? AND status = 'pending'", (conversation_id,)
            ).fetchall()
        )
        for r in rows:
            with contextlib.suppress(ApprovalError):
                await self._close(r["id"], "expired", None, EXPIRED, "hub")
        return len(rows)

    async def pending(self) -> list[dict]:
        rows = await self._db.acall(
            lambda conn: conn.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at").fetchall()
        )
        return [await self._with_preview(_row(r)) for r in rows]

    async def get(self, approval_id: str) -> dict:
        row = await self._db.acall(
            lambda conn: conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        )
        if row is None:
            raise ApprovalError(f"no such approval: {approval_id}")
        return await self._with_preview(_row(row))

    async def _with_preview(self, row: dict) -> dict:
        root = None
        if row["project_id"]:
            found = await self._db.acall(
                lambda conn: conn.execute("SELECT path FROM projects WHERE id = ?", (row["project_id"],)).fetchone()
            )
            root = Path(found["path"]) if found else None
        return row | {"preview": await asyncio.to_thread(preview, row["tool"], row["input"], root)}

    async def _close(self, approval_id: str, status: str, scope: str | None, comment: str, by: str) -> dict:
        def write(conn: sqlite3.Connection) -> sqlite3.Row | None:
            # a destructive command has no grant key: "for this chat" is then allowed this once only (D17)
            updated = conn.execute(
                "UPDATE approvals SET status = ?, scope = CASE WHEN grant_key IS NULL AND ? = 'chat' THEN 'once'"
                " ELSE ? END, comment = ?, decided_at = ?, decided_by = ? WHERE id = ? AND status = 'pending'",
                (status, scope, scope, comment or None, utc_now(), by, approval_id),
            ).rowcount
            return conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone() if updated else None

        found = await self._db.acall(write)
        if found is None:
            raise ApprovalError(f"no open approval {approval_id}: it was answered already, or never asked")
        row = _row(found)
        await self._bus.publish("approval.decided", {"approval": row}, project_id=row["project_id"])
        future = self._waiting.get(approval_id)
        if future is not None and not future.done():
            allowed = status == "allowed"
            future.set_result(Decision(allowed, "" if allowed else comment or DENIED))
        return row
