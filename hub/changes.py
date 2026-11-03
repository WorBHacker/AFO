"""Files the hub's models write (stage 5, D17): write_file and edit_file, each kept as a change that can be undone.

A model proposes a write; the chat asks the user with a diff (hub/approvals.py) unless the chat is trusted or a rule
covers it, then the hub checks that the file is still what the user saw, writes it, and records the change: the file
before and after, by hash, the contents kept once each in data/snapshots. Any change can be undone while the file is
still as the change left it (or forced). Only inside the chat's projects, never a secret file or git's own folder,
UTF-8 text only. A write is whole or not at all (beside the file, then in its place), and an existing file keeps
its byte order mark and its line ends (D46). Events: file.changed, file.reverted - both in the feed.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import utc_now
from .projects import Project, ProjectError, linked_secret, resolve_inside
from .secret_filter import is_secret_file

MAX_BYTES = 2 * 1024 * 1024
DIFF_LINES = 400

WRITE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "edit_file",
        "description": "Change a file of a project: replace old_text with new_text. old_text must appear in the file "
        "exactly once (copy it from read_file, without the line numbers) - or set replace_all. The user is asked "
        "first and sees the diff; if they say no, the result says why. Prefer it to write_file for changing part of a "
        "file.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "The project's name, as listed in the system prompt."},
                "path": {"type": "string", "description": "The file, relative to the project's root."},
                "old_text": {"type": "string", "description": "The exact text to replace."},
                "new_text": {"type": "string", "description": "What to put in its place."},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default: just one)."},
            },
            "required": ["project", "path", "old_text", "new_text"],
        },
    },
    {
        "name": "write_file",
        "description": "Create a file in a project, or replace all of its content - an existing file only after you "
        "read it in this chat (read_file), else it is refused. The user is asked first and sees the diff. To change "
        "part of an existing file use edit_file.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "The project's name, as listed in the system prompt."},
                "path": {"type": "string", "description": "The file, relative to the project's root."},
                "content": {"type": "string", "description": "The whole new content of the file."},
            },
            "required": ["project", "path", "content"],
        },
    },
]
WRITE_TOOL_NAMES = {tool["name"] for tool in WRITE_TOOLS}


class WriteError(Exception):
    """The message goes back to the model (or to the user, for an undo)."""


@dataclass(frozen=True)
class Planned:
    """A write worked out but not done: the file's bytes before (None: a new file) and after."""

    path: str  # relative to the project, with /
    target: Path
    before: bytes | None
    after: bytes


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def keep(folder: Path, data: bytes) -> str:
    """Stores a version of a file by its hash (one copy of each); returns the hash."""
    name = digest(data)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if not target.exists():
        target.write_bytes(data)
    return name


def load(folder: Path, name: str) -> bytes:
    try:
        return (folder / name).read_bytes()
    except OSError as exc:
        raise WriteError(f"the kept version {name[:12]} is gone from data/snapshots") from exc


def _target(project: Project, args: dict[str, Any]) -> tuple[str, Path]:
    rel = args.get("path")
    if not isinstance(rel, str) or not rel.strip():
        raise WriteError("`path` must be a file's path inside the project")
    try:
        target = resolve_inside(project.root, rel)
    except ProjectError as exc:
        raise WriteError(str(exc)) from exc
    shown = target.relative_to(project.root).as_posix()
    if not shown or target.is_dir():
        raise WriteError(f"{rel} is a folder, not a file")
    if is_secret_file(shown) or is_secret_file(rel) or linked_secret(target):  # what it is, as asked, its links
        raise WriteError(f"{shown} holds secrets (keys, passwords): models may not write it")
    if shown == ".git" or shown.startswith(".git/"):
        raise WriteError("git's own folder is not for writing; use files of the project")
    return shown, target


def _current(target: Path) -> bytes | None:
    try:
        if target.stat().st_size > MAX_BYTES:
            raise WriteError(f"{target.name} is larger than 2 MB: the hub does not change it")
        return target.read_bytes()
    except FileNotFoundError:
        return None


def _text_of(name: str, data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WriteError(f"{name} is not UTF-8 text: the hub does not change it") from exc


def line_end(text: str) -> str:
    """The file's own line end: CRLF when most of its lines have it, else LF."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf > text.count("\n") - crlf else "\n"


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def as_the_file_has_it(before: bytes, old_text: str, content: str) -> bytes:
    """A whole new text written the way the file was (D46): its byte order mark and its line ends - a model writes
    \\n and no mark, and a Windows file should not change on every line for that."""
    content = content.removeprefix("﻿")
    content = _crlf(content) if line_end(old_text) == "\r\n" else content.replace("\r\n", "\n")
    return (b"\xef\xbb\xbf" if before.startswith(b"\xef\xbb\xbf") else b"") + content.encode("utf-8")


def write_atomic(target: Path, data: bytes) -> None:
    """The whole file or nothing (D46): written beside it, then put in its place - a hub stopped in the middle leaves
    the old file, not half of the new one. Windows lets another program (an antivirus, an editor) hold a file for a
    moment: the swap is tried again a few times."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".localchat", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if target.exists():
            shutil.copymode(target, temp)
        for wait in (0.05, 0.2, 0.5, None):
            try:
                os.replace(temp, target)
                return
            except PermissionError:
                if wait is None:
                    raise
                time.sleep(wait)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def plan(tool: str, project: Project, args: dict[str, Any]) -> Planned:
    """What a write would do, checked: the model's mistakes come back as WriteError for it to read."""
    rel, target = _target(project, args)
    before = _current(target)
    if tool == "write_file":
        content = args.get("content")
        if not isinstance(content, str):
            raise WriteError("`content` must be the file's text")
        if before is None:
            return Planned(rel, target, None, content.encode("utf-8"))
        return Planned(rel, target, before, as_the_file_has_it(before, _text_of(rel, before), content))
    old, new = args.get("old_text"), args.get("new_text")
    if not isinstance(old, str) or not old or not isinstance(new, str):
        raise WriteError("`old_text` (not empty) and `new_text` must be text")
    if before is None:
        raise WriteError(f"{rel} does not exist: create it with write_file")
    text = _text_of(rel, before)
    if line_end(text) == "\r\n":  # the model writes \n; a file with Windows line ends gets them in what it adds too
        new = _crlf(new)
        if old not in text:
            old = _crlf(old)
    count = text.count(old)
    if count == 0:
        raise WriteError(f"old_text is not in {rel}: read the file again (without the line numbers) and copy it"
                         + _closest(text, old))  # fmt: skip
    if count > 1 and not args.get("replace_all"):
        raise WriteError(f"old_text appears {count} times in {rel}: give more of the text around it, or replace_all")
    after = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
    if after == text:
        raise WriteError("new_text is the same as old_text: nothing would change")
    return Planned(rel, target, before, after.encode("utf-8"))


def _closest(text: str, old: str) -> str:
    """The lines of the file most like old_text's first line - a model that misspelt it can copy them."""
    first = next((line.strip() for line in old.splitlines() if line.strip()), "")
    lines = text.splitlines()
    found = difflib.get_close_matches(first, [line.strip() for line in lines], n=2, cutoff=0.5) if first else []
    shown = [f"{n}| {line}" for n, line in enumerate(lines, 1) if line.strip() in found][:2]
    return ". The closest lines in it: " + "; ".join(shown) if shown else ""


def apply(planned: Planned, folder: Path) -> tuple[str | None, str]:
    """Writes the file, if it is still as it was planned against; returns the hashes of before and after."""
    if _current(planned.target) != planned.before:
        raise WriteError(f"{planned.path} changed while the user was deciding: read it again and propose anew")
    before = keep(folder, planned.before) if planned.before is not None else None
    after = keep(folder, planned.after)
    write_atomic(planned.target, planned.after)
    return before, after


def diff(path: str, before: bytes | None, after: bytes | None) -> str:
    """A unified diff between two versions (None: no file), cut at DIFF_LINES lines."""
    lines = list(difflib.unified_diff(
        (before or b"").decode("utf-8", "replace").splitlines(keepends=True),
        (after or b"").decode("utf-8", "replace").splitlines(keepends=True),
        fromfile=f"a/{path}" if before is not None else "/dev/null",
        tofile=f"b/{path}" if after is not None else "/dev/null",
    ))  # fmt: skip
    text = "".join(line if line.endswith("\n") else line + "\n" for line in lines[:DIFF_LINES])
    return text + (f"… ещё {len(lines) - DIFF_LINES} строк\n" if len(lines) > DIFF_LINES else "")


def summary(planned: Planned) -> str:
    """What the model reads after its write: what changed, in lines."""
    old = (planned.before or b"").decode("utf-8", "replace").splitlines()
    new = planned.after.decode("utf-8", "replace").splitlines()
    added = removed = 0
    for line in difflib.unified_diff(old, new, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    made = "created" if planned.before is None else "changed"
    return f"{planned.path} {made}: +{added} -{removed} lines"


# --- the record ---


def record(
    conn: sqlite3.Connection,
    project_id: str,
    planned: Planned,
    hashes: tuple[str | None, str],
    tool: str,
    conversation_id: str | None,
    approval_id: str | None,
) -> dict[str, Any]:
    change_id = conn.execute(
        "INSERT INTO file_changes (project_id, path, before_hash, after_hash, tool, conversation_id, approval_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, planned.path, hashes[0], hashes[1], tool, conversation_id, approval_id),
    ).lastrowid
    return get_change(conn, change_id)


def get_change(conn: sqlite3.Connection, change_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT f.*, c.title AS chat FROM file_changes f LEFT JOIN conversations c ON c.id = f.conversation_id"
        " WHERE f.id = ?",
        (change_id,),
    ).fetchone()
    if row is None:
        raise WriteError(f"no such change: {change_id}")
    return dict(row)


def list_changes(conn: sqlite3.Connection, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT f.*, c.title AS chat FROM file_changes f LEFT JOIN conversations c ON c.id = f.conversation_id"
        " WHERE f.project_id = ? ORDER BY f.id DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def revert(change: dict[str, Any], project: Project, folder: Path, *, force: bool = False) -> None:
    """Puts the file back as it was before the change (removes a file the change created). Refused when the file has
    changed since - another change, or the user's own edit - unless forced."""
    if change["reverted_at"]:
        raise WriteError("this change is undone already")
    target = resolve_inside(project.root, change["path"])
    current = _current(target)
    if not force and (current is None or digest(current) != change["after_hash"]):
        raise WriteError(f"{change['path']} has changed since: undoing would lose those changes")
    if change["before_hash"] is None:
        target.unlink(missing_ok=True)
    else:
        write_atomic(target, load(folder, change["before_hash"]))


def mark_reverted(conn: sqlite3.Connection, change_id: int) -> dict[str, Any]:
    conn.execute("UPDATE file_changes SET reverted_at = ? WHERE id = ?", (utc_now(), change_id))
    return get_change(conn, change_id)
