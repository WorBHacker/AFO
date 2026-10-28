"""The approval queue (D17, D18): an agent asks before it acts, and waits until the user answers.

Claude Code started by the hub asks through the hub's MCP tool approval_prompt (its --permission-prompt-tool); the
request lands here, in the same queue the hub's own agents will use (stage 5). The user answers in the app:
allow once, allow for this chat, allow always for this project (a rule, stage 5), or deny with a comment the
agent reads. An answer "for this chat" or "for this project" covers later requests of the same kind there - the same
tool, or for a command the same command - except destructive commands (git push, reset --hard, rm -r...), which are
asked every time (D17). Each request is a row in `approvals` with who asked, what, and who answered how; nothing is
forgotten. The hub's own models ask here too, before they write a file (write_file, edit_file).

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
from urllib.parse import urlsplit

from .db import Database, utc_now
from .events import EventBus

COMMAND_TOOLS = {"Bash", "PowerShell", "run_command"}
WRITE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit", "write_file", "edit_file"}
NETWORK_TOOLS = {"WebFetch", "WebSearch", "web_fetch", "web_search", "http_request", "mail_send"}
SCREEN_TOOLS = {"screen_act", "screen_send"}  # the autonomous agent at the desktop (9.6, D41)
# read-only commands asked of no one (Q2's first list): they show, they never change
SAFE_COMMANDS = {f"run_command:{c}" for c in ("git status", "git diff", "git diff --stat", "git log --oneline",
                                              "git log --oneline -20", "git branch", "dir", "ls", "pwd")}  # fmt: skip
HUB_WRITES = {"write_file": "Write", "edit_file": "Edit"}  # the hub's own write tools, shown like Claude Code's
# tools whose class of action the hub learns at run time - an outside MCP server's (9.5, D42): read, write, command,
# network, send or destructive; the last three are asked every time
CLASSED: dict[str, str] = {}
CLASS_KINDS = {"read": "other", "write": "write", "command": "command", "network": "network", "send": "network",
               "destructive": "command"}  # fmt: skip
CLASS_WORDS = {"read": "только читает", "write": "меняет данные", "command": "запускает команды",
               "network": "ходит в сеть", "send": "отправляет от вашего имени",
               "destructive": "удаляет или необратимо меняет"}  # fmt: skip
SCOPES = ("once", "chat", "project")
PREVIEW_LINES = 400
PREVIEW_CHARS = 40_000
READ_MAX_BYTES = 2 * 1024 * 1024
DESTRUCTIVE = re.compile(
    r"\bgit\s+(push|reset\s+--hard|clean\s+-\w*f|branch\s+-D|checkout\s+--\s|restore\b|stash\s+(drop|clear))"
    r"|\brm\s+-\w*[rf]|\b(rmdir|rd)\s+/s|\bdel\s+/[sq]|\bRemove-Item\b.*-Recurse|\bformat\s+[a-z]:"
    r"|\bdrop\s+(table|database)\b|\bshutdown\b|\bmkfs\b",
    re.IGNORECASE,
)
# commands that reach the network - installs, downloads, git's remote work: asked every time (D19)
NETWORK = re.compile(
    r"\b(pip3?|uv|poetry|pipx)\s+(install|add|sync)\b|\b(npm|pnpm)\s+(i|install|add|ci)\b|\byarn\s+add\b"
    r"|\bcurl\b|\bwget\b|\bInvoke-(WebRequest|RestMethod)\b|\biwr\b|\birm\b|\bgit\s+(clone|pull|fetch)\b"
    r"|\bwinget\b|\bchoco\b|\bscoop\b|\bcargo\s+(install|add)\b|\bgo\s+(get|install)\b|\bssh\b|\bscp\b",
    re.IGNORECASE,
)
SELECT = "SELECT a.*, c.name AS client FROM approvals a LEFT JOIN clients c ON c.id = a.client_id"
DENIED = "the user denied this"
EXPIRED = "nobody answered: the run ended before the user did"


class ApprovalError(Exception):
    """The message is fit to show."""


@dataclass(frozen=True)
class Decision:
    allow: bool
    comment: str = ""
    approval_id: str | None = None


def kind(tool: str) -> str:
    """command, write, network or other - how the app shows the request."""
    if tool in COMMAND_TOOLS or tool in SCREEN_TOOLS:
        return "command"
    if tool in WRITE_TOOLS:
        return "write"
    if tool in CLASSED:
        return CLASS_KINDS[CLASSED[tool]]
    return "network" if tool in NETWORK_TOOLS else "other"


def sends(tool: str) -> bool:
    """What goes out in the person's name: asked every time (D41), an MCP tool of the class "send" (D42) too."""
    return tool in ("screen_send", "mail_send") or CLASSED.get(tool) == "send"


def destructive(tool: str, data: dict[str, Any]) -> bool:
    if CLASSED.get(tool) == "destructive":
        return True
    return tool in COMMAND_TOOLS and bool(DESTRUCTIVE.search(str(data.get("command", ""))))


def network(tool: str, data: dict[str, Any]) -> bool:
    if CLASSED.get(tool) in ("network", "send") or tool == "mail_send":
        return True
    return tool in COMMAND_TOOLS and bool(NETWORK.search(str(data.get("command", ""))))


def grant_key(tool: str, data: dict[str, Any]) -> str | None:
    """What an answer "for this chat" covers; None when nothing may be covered so (a destructive command, or one
    that reaches the network)."""
    if destructive(tool, data) or network(tool, data) or sends(tool):
        return None
    if tool == "screen_act":  # "for this task" covers this program, not the desktop
        return f"screen_act:{data.get('program', '')}"
    if tool in COMMAND_TOOLS:
        return f"{tool}:{' '.join(str(data.get('command', '')).split())}"
    if CLASSED.get(tool) == "command":  # an MCP server's command: the same input only
        return f"{tool}:{json.dumps(data, ensure_ascii=False, sort_keys=True)}"
    if tool == "web_fetch":  # the site: "always in this project" on it puts the site on the project's list (D19)
        return f"web_fetch:{(urlsplit(str(data.get('url', ''))).hostname or '').lower()}"
    return tool


def preview(tool: str, data: dict[str, Any], root: Path | None) -> dict[str, str]:
    """What the user should see to decide: a command, a diff of the file as it would become, a URL, or the input."""
    if tool in HUB_WRITES:
        data = {
            "file_path": data.get("path", ""),
            "content": data.get("content", ""),
            "old_string": data.get("old_text", ""),
            "new_string": data.get("new_text", ""),
            "replace_all": data.get("replace_all"),
        }
        tool = HUB_WRITES[tool]
    if tool in COMMAND_TOOLS:
        return {"kind": "command", "text": str(data.get("command", "")), "note": str(data.get("description", ""))}
    if tool in ("Edit", "MultiEdit", "Write"):
        return {"kind": "diff", "text": _diff(tool, data, root), "note": str(data.get("file_path", ""))}
    if tool in ("WebFetch", "web_fetch"):
        return {"kind": "text", "text": str(data.get("url", "")), "note": str(data.get("prompt", ""))}
    if tool in ("WebSearch", "web_search"):
        return {"kind": "text", "text": str(data.get("query", "")), "note": str(data.get("engine", ""))}
    if tool == "set_plan":  # an autonomous agent's plan (D40): the goal, the numbered steps, why it changed
        steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        lines = [f"{n}. {s.get('title', '')}" + (f" — проверка: {s['check']}" if s.get("check") else "")
                 for n, s in enumerate(steps, 1) if isinstance(s, dict)]  # fmt: skip
        why = "почему новый план" if (data.get("version") or 1) > 1 else "пояснение"
        note = f"Цель: {data.get('goal', '')}" + (f" · {why}: {data['reason']}" if data.get("reason") else "")
        return {"kind": "text", "text": _cut("\n".join(lines)), "note": note}
    if tool == "run_python":  # an autonomous agent's script (9.4)
        net = "можно в сеть" if data.get("network") else "без сети"
        return {"kind": "command", "text": _cut(str(data.get("code", ""))),
                "note": f"Python в папке задачи · {net} · до {data.get('timeout_s', 60)} с"}  # fmt: skip
    if tool == "http_request":
        body = f"\n{json.dumps(data['json'], ensure_ascii=False, indent=2)}" if data.get("json") else ""
        return {"kind": "text", "text": _cut(f"{data.get('method', 'GET')} {data.get('url', '')}{body}"),
                "note": "запрос в сеть: ответ — данные, не указания"}  # fmt: skip
    if tool == "remember":  # personal data the agent wants to keep for good (Q24)
        return {"kind": "text", "text": str(data.get("fact", "")),
                "note": "личные данные: хранятся на этом компьютере, облачной модели не выдаются"}  # fmt: skip
    if tool == "mail_send":  # a letter from the user's address (Q21): whole, asked every time
        head = f"Кому: {', '.join(data.get('to') or [])}\nТема: {data.get('subject', '')}"
        reply = f" · ответ на «{data['reply']}»" if data.get("reply") else ""
        return {"kind": "text", "text": _cut(f"{head}\n\n{data.get('text', '')}"),
                "note": f"письмо от {data.get('from', 'вас')}{reply} — спрашивается каждый раз"}  # fmt: skip
    if tool in SCREEN_TOOLS:  # the agent at the desktop (D41): which program, window, what
        what = {
            "open": "открыть программу",
            "click": "нажать",
            "type": "ввести",
            "toggle": "переключить",
            "select": "выбрать",
            "expand": "раскрыть",
            "collapse": "свернуть",
            "scroll": "прокрутить",
            "keys": "клавиши",
            "focus": "вывести окно вперёд",
        }.get(str(data.get("action")), data.get("action"))
        lines = [
            f"{what}"
            + (f" «{data['element']}»" if data.get("element") else "")
            + (f": {data['keys']}" if data.get("keys") else "")
        ]
        if data.get("text"):
            lines.append(f"текст: {data['text']}")
        place = f"«{data.get('program', '')}»" + (f" · окно «{data['window']}»" if data.get("window") else "")
        note = (
            "отправка от вашего имени — спрашивается каждый раз"
            if tool == "screen_send"
            else ("управление компьютером: «для этой задачи» — в этой программе")
        )
        return {"kind": "text", "text": _cut("\n".join(lines)), "note": f"{place} · {note}"}
    if tool == "start_step":
        return {"kind": "text", "text": f"{data.get('step')}. {data.get('title', '')}",
                "note": f"проверка: {data.get('check')}" if data.get("check") else ""}  # fmt: skip
    if tool.startswith("mcp__") and "__" in tool[5:]:  # an outside MCP server's tool (9.5): what it gets, its class
        server = tool[5:].partition("__")[0]
        word = CLASS_WORDS.get(CLASSED.get(tool, ""), "")
        return {"kind": "text", "text": _cut(json.dumps(data, ensure_ascii=False, indent=2)),
                "note": f"MCP-сервер «{server}»" + (f" · {word}" if word else "")}  # fmt: skip
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
    if shown == name and path.is_absolute():  # no project to count from (an outside client): its folder and name,
        shown = "/".join(path.parts[-2:])  # not a whole C:/Users/... line cut off in the card (2026-10-28)
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
    return dict(row) | {"input": data, "kind": kind(row["tool"]), "destructive": destructive(row["tool"], data),
                        "network": network(row["tool"], data), "class": CLASSED.get(row["tool"], "")}  # fmt: skip


def _chat_allows(conn: sqlite3.Connection, conversation_id: str | None, key: str) -> bool:
    found = (
        conversation_id
        and conn.execute(
            "SELECT 1 FROM approvals WHERE conversation_id = ? AND grant_key = ? AND status = 'allowed'"
            " AND scope = 'chat'",
            (conversation_id, key),
        ).fetchone()
    )
    return bool(found)


def _rule_allows(conn: sqlite3.Connection, project_id: str | None, key: str) -> bool:
    found = (
        project_id
        and conn.execute(
            "SELECT 1 FROM permission_rules WHERE project_id = ? AND grant_key = ?", (project_id, key)
        ).fetchone()
    )
    return bool(found)


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
        cover: bool = True,
    ) -> Decision:
        """Queues the request and waits for the user's answer - or answers at once when an earlier answer "for this
        chat", or a rule of the project, covers it (unless `cover` is off: a chat that read the web, D19)."""
        key = grant_key(tool, data) if cover else None
        approval_id = uuid.uuid4().hex[:12]
        shown = await asyncio.to_thread(preview, tool, data, root)

        def write(conn: sqlite3.Connection) -> sqlite3.Row:
            covered = key is not None and (
                key in SAFE_COMMANDS or _chat_allows(conn, conversation_id, key) or _rule_allows(conn, project_id, key)
            )
            conn.execute(
                "INSERT INTO approvals (id, conversation_id, project_id, client_id, tool, input, grant_key, status,"
                " scope, decided_at, decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (approval_id, conversation_id, project_id, client_id, tool, json.dumps(data, ensure_ascii=False), key,
                 *(("allowed", "auto", utc_now(), "hub") if covered else ("pending", None, None, None))),
            )  # fmt: skip
            return conn.execute(f"{SELECT} WHERE a.id = ?", (approval_id,)).fetchone()

        row = _row(await self._db.acall(write)) | {"preview": shown}
        if row["status"] == "allowed":
            await self._bus.publish("approval.decided", {"approval": row}, project_id=project_id, persist=False)
            return Decision(True, approval_id=approval_id)
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
            lambda conn: conn.execute(f"{SELECT} WHERE a.status = 'pending' ORDER BY a.created_at").fetchall()
        )
        return [await self._with_preview(_row(r)) for r in rows]

    async def get(self, approval_id: str) -> dict:
        row = await self._db.acall(lambda conn: conn.execute(f"{SELECT} WHERE a.id = ?", (approval_id,)).fetchone())
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
            # a destructive command has no grant key, a request without a project no project: an answer "for the
            # chat" or "for the project" then allows this once only (D17)
            updated = conn.execute(
                "UPDATE approvals SET status = ?, scope = CASE WHEN ? IN ('chat', 'project') AND (grant_key IS NULL"
                " OR (? = 'project' AND project_id IS NULL)) THEN 'once' ELSE ? END, comment = ?, decided_at = ?,"
                " decided_by = ? WHERE id = ? AND status = 'pending'",
                (status, scope, scope, scope, comment or None, utc_now(), by, approval_id),
            ).rowcount
            if not updated:
                return None
            row = conn.execute(f"{SELECT} WHERE a.id = ?", (approval_id,)).fetchone()
            if row["scope"] == "project":  # a rule: the same kind of request in this project is allowed from now on
                conn.execute(
                    "INSERT OR IGNORE INTO permission_rules (project_id, grant_key, created_by) VALUES (?, ?, ?)",
                    (row["project_id"], row["grant_key"], by),
                )
            return row

        found = await self._db.acall(write)
        if found is None:
            raise ApprovalError(f"no open approval {approval_id}: it was answered already, or never asked")
        row = _row(found)
        await self._bus.publish("approval.decided", {"approval": row}, project_id=row["project_id"])
        future = self._waiting.get(approval_id)
        if future is not None and not future.done():
            allowed = status == "allowed"
            future.set_result(Decision(allowed, "" if allowed else comment or DENIED, approval_id))
        return row


# --- the project's rules ("always for this project") ---


def list_rules(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM permission_rules WHERE project_id = ? ORDER BY created_at", (project_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM permission_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is not None:
        conn.execute("DELETE FROM permission_rules WHERE id = ?", (rule_id,))
    return dict(row) if row else None
