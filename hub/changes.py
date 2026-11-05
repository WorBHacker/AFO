"""Files the hub's models write (stage 5, D17): write_file and edit_file, each kept as a change that can be undone.

A model proposes a write; the chat asks the user with a diff (hub/approvals.py) unless the chat is trusted or a rule
covers it, then the hub checks that the file is still what the user saw, writes it, and records the change: the file
before and after, by hash, the contents kept once each in data/snapshots. Any change can be undone while the file is
still as the change left it (or forced). Only inside the chat's projects, never a secret file or git's own folder,
UTF-8 text only. A write is whole or not at all (beside the file, then in its place), and an existing file keeps
its byte order mark and its line ends (D46). Events: file.changed, file.reverted - both in the feed.

The user may allow a write in part (17.2, D51): its hunks are the ones its unified diff shows, and the file is rebuilt
along the diff - a chosen hunk's lines as the model wrote them, the others as the file has them; the model is told
where the rest are (`left_out`).

The user's own saves from the window's editor take the same way (D47, step 14.1; D29: one way of writing): checked
against the version the editing began from, written in the file's encoding (cp1251 and UTF-16 too), kept as a
change of the tool `save` - «вы» in «Изменения» - and undone like a model's; a secret file's content is not kept.
"""

from __future__ import annotations

import dataclasses
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

from common.i18n import tr

from .db import utc_now
from .projects import Project, ProjectError, linked_secret, resolve_inside
from .secret_filter import is_secret_file
from .symbols import CONTAINER_KINDS, language_of, parse

MAX_BYTES = 2 * 1024 * 1024
DIFF_LINES = 400
CONTEXT = 3  # unchanged lines around a change in a hunk, as a unified diff has them (difflib's own default)
ENCLOSING = {"function", "method", *CONTAINER_KINDS}  # definitions that name where a hunk is (not «x = 1»)
SAVE_ENCODINGS = ("utf-8", "utf-8 BOM", "cp1251", "utf-16")  # as projects.read_file names them
SAVE_TOOL = "save"  # a change the user made in the window's editor (D47): «вы» in «Изменения»

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
CLAUDE_WRITES = {"Write": "write_file", "Edit": "edit_file"}  # Claude Code's own, worked out as the hub's (17.3)


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


def _target(root: Path, args: dict[str, Any]) -> tuple[str, Path]:
    rel = args.get("path")
    if not isinstance(rel, str) or not rel.strip():
        raise WriteError("`path` must be a file's path inside the project")
    try:
        target = resolve_inside(root, rel)
    except ProjectError as exc:
        raise WriteError(str(exc)) from exc
    shown = target.relative_to(root).as_posix()
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


# --- the user's own save, from the window's editor (D47, step 14.1) ---


class Conflict(WriteError):
    """The file on disk is not the one the editing began from: `reason` - changed, gone or exists."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _decoded(data: bytes, encoding: str) -> str:
    """A file's bytes as the editor showed them (projects.read_file's names for its encodings)."""
    if encoding == "utf-16":
        return data.decode("utf-16", "replace")
    return data.removeprefix(b"\xef\xbb\xbf").decode("cp1251" if encoding == "cp1251" else "utf-8", "replace")


def encoded(text: str, encoding: str, before: bytes | None) -> bytes:
    """The editor's text as the file has it (D47, step 14.1): the line end most of its lines had (a new file \\n), its
    byte order mark, its encoding - cp1251 stays cp1251, UTF-16 keeps its byte order. A character the encoding has no
    place for is said, not lost."""
    if encoding not in SAVE_ENCODINGS:
        raise WriteError(f"unknown encoding {encoding!r}: {', '.join(SAVE_ENCODINGS)}")
    text = text.replace("\r\n", "\n").removeprefix("﻿")
    if before is not None and line_end(_decoded(before, encoding)) == "\r\n":
        text = _crlf(text)
    if encoding == "utf-16":
        big = before is not None and before.startswith(b"\xfe\xff")
        return (b"\xfe\xff" + text.encode("utf-16-be")) if big else (b"\xff\xfe" + text.encode("utf-16-le"))
    if encoding == "cp1251":
        try:
            return text.encode("cp1251")
        except UnicodeEncodeError as exc:
            line = text.count("\n", 0, exc.start) + 1
            raise WriteError(f"«{text[exc.start]}» (line {line}) has no place in cp1251: save the file as UTF-8, "
                             "or leave the character out") from exc  # fmt: skip
    return (b"\xef\xbb\xbf" if encoding == "utf-8 BOM" else b"") + text.encode("utf-8")


def plan_save(project: Project, rel: str, text: str, encoding: str, base: str) -> Planned:
    """The user's save worked out: refused when the file on disk is not the version `base` (its sha256; "" - a new
    file) - another program, a model or a command changed it meanwhile, and the user decides (Conflict). The user may
    save any file of the project, a secret one too; never the hub's own data or git's own folder."""
    if encoding not in SAVE_ENCODINGS:  # the request itself, before the disk is looked at
        raise WriteError(f"unknown encoding {encoding!r}: {', '.join(SAVE_ENCODINGS)}")
    try:
        target = resolve_inside(project.root, rel)
    except ProjectError as exc:
        raise WriteError(str(exc)) from exc
    shown = target.relative_to(project.root).as_posix()
    if not shown or target.is_dir():
        raise WriteError(f"{rel} is a folder, not a file")
    if shown == ".git" or shown.startswith(".git/"):
        raise WriteError("git's own folder is not edited here")
    before = _current(target)
    if before is None and base:
        raise Conflict(f"{shown} is gone from the disk since it was opened", "gone")
    if before is not None and digest(before) != base:
        raise Conflict(f"{shown} has changed on the disk since it was opened" if base else f"{shown} exists already",
                       "changed" if base else "exists")  # fmt: skip
    after = encoded(text, encoding, before)
    if len(after) > MAX_BYTES:
        raise WriteError(f"{shown} would be larger than 2 MB: the hub does not write it")
    return Planned(shown, target, before, after)


def save(planned: Planned, folder: Path) -> tuple[str | None, str] | None:
    """Writes the user's save (whole or not at all) and keeps both versions for «Изменения»; None for a secret file:
    its content is never kept (D10) - another client may read a change's diff - so there is no change to undo."""
    if is_secret_file(planned.path) or linked_secret(planned.target):
        if _current(planned.target) != planned.before:
            raise Conflict(f"{planned.path} has changed on the disk since it was opened", "changed")
        write_atomic(planned.target, planned.after)
        return None
    try:
        return apply(planned, folder)
    except WriteError as exc:
        raise Conflict(str(exc), "changed") from exc


def plan(tool: str, project: Project, args: dict[str, Any]) -> Planned:
    """What a write would do, checked: the model's mistakes come back as WriteError for it to read."""
    return plan_at(project.root, tool, args)


def as_hub_write(tool: str, data: dict[str, Any], root: Path) -> tuple[str, dict[str, Any]] | None:
    """A write as the hub's write_file / edit_file in the project at `root` (17.3): the hub's own as it is, Claude
    Code's Write and Edit by their fields - so the card's diff, its hunks and a part of it are worked out one way.
    None for another tool, or for a file outside the project."""
    if tool in WRITE_TOOL_NAMES:
        return tool, data
    if tool not in CLAUDE_WRITES:
        return None
    path = Path(str(data.get("file_path") or ""))
    try:
        rel = Path(os.path.relpath(path if path.is_absolute() else root / path, root))
    except ValueError:  # another drive
        return None
    if not rel.parts or rel.parts[0] == "..":
        return None
    if tool == "Write":
        return "write_file", {"path": rel.as_posix(), "content": data.get("content", "")}
    return "edit_file", {"path": rel.as_posix(), "old_text": data.get("old_string", ""),
                         "new_text": data.get("new_string", ""),
                         "replace_all": bool(data.get("replace_all"))}  # fmt: skip


def plan_at(root: Path, tool: str, args: dict[str, Any]) -> Planned:
    """plan() in the project at `root` - also what the approval card shows (hub/approvals.py, 17.2)."""
    rel, target = _target(root, args)
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
    text = "".join(_shown(line) for line in lines[:DIFF_LINES])
    return text + (tr("… ещё {count} строк", count=len(lines) - DIFF_LINES) + "\n" if len(lines) > DIFF_LINES else "")


def _lines(data: bytes | None) -> list[str]:
    return (data or b"").decode("utf-8", "replace").splitlines(keepends=True)


def _diffed(planned: Planned) -> tuple[list[tuple], list[list[tuple]]]:
    """The write's opcodes, all of them, and its hunks as difflib groups them for a unified diff (diff(), the card's
    diff) - those with a change. All are read first: grouping trims the first and last opcodes in difflib's own list."""
    matcher = difflib.SequenceMatcher(None, _lines(planned.before), _lines(planned.after))
    ops = list(matcher.get_opcodes())
    return ops, [g for g in matcher.get_grouped_opcodes(CONTEXT) if any(op[0] != "equal" for op in g)]


def _shown(line: str) -> str:
    """A diff's line as it is read: ending in \\n whatever the file's own end (CRLF) - those are its bytes."""
    return line.rstrip("\r\n") + "\n"


def _range(start: int, stop: int) -> str:
    """One side of a hunk's @@ line, as difflib writes it."""
    if stop - start == 1:
        return str(start + 1)
    return f"{start if stop == start else start + 1},{stop - start}"


def hunks(planned: Planned) -> list[dict[str, Any]]:
    """The write's hunks (17.2, D51): those its unified diff shows, in that order - the index, the @@ line, the
    lines (context, removed, added)."""
    old, new = _lines(planned.before), _lines(planned.after)
    out = []
    for index, group in enumerate(_diffed(planned)[1]):
        first, last = group[0], group[-1]
        lines = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [" " + line for line in old[i1:i2]]
            else:
                lines += ["-" + line for line in old[i1:i2]] + ["+" + line for line in new[j1:j2]]
        out.append({"index": index, "header": f"@@ -{_range(first[1], last[2])} +{_range(first[3], last[4])} @@",
                    "text": "".join(_shown(line) for line in lines)})  # fmt: skip
    return out


def hunks_diff(planned: Planned, found: list[dict[str, Any]]) -> str:
    """The unified diff of the write made of its hunks - what diff() gives, without comparing the file again."""
    if not found:
        return ""
    head = f"--- {'a/' + planned.path if planned.before is not None else '/dev/null'}\n+++ b/{planned.path}\n"
    return head + "".join(f"{h['header']}\n{h['text']}" for h in found)


def compose(planned: Planned, chosen: set[int]) -> Planned:
    """The write with only the hunks `chosen` (17.2): the file rebuilt along the diff - in a chosen hunk the lines
    as the model wrote them, elsewhere as the file has them. Nothing is shifted or patched, so nothing lands in the
    wrong place; the file's mark and line ends are in both versions already (D46)."""
    ops, groups = _diffed(planned)
    taken = {op for index, group in enumerate(groups) if index in chosen for op in group if op[0] != "equal"}
    old, new = _lines(planned.before), _lines(planned.after)
    out: list[str] = []
    for op in ops:
        _tag, i1, i2, j1, j2 = op
        out += new[j1:j2] if op in taken else old[i1:i2]
    return dataclasses.replace(planned, after="".join(out).encode("utf-8"))


def left_out(planned: Planned, chosen: set[int]) -> list[str]:
    """Where the hunks not chosen are, in the file as it was - what the model reads (17.2): «line 14 (in
    Box.second)», «lines 3-5», «after line 14»; the definition by the symbol index's parser when the file is code."""
    language = language_of(planned.path)
    defs = [d for d in (parse(planned.before, language) if language and planned.before else []) if d.kind in ENCLOSING]
    out = []
    for index, group in enumerate(_diffed(planned)[1]):
        if index in chosen:
            continue
        changed = [op for op in group if op[0] != "equal"]
        start, end = changed[0][1], changed[-1][2]  # in the old file: 0-based, the end not counted
        if start == end:  # only added: between two lines
            where = f"after line {start}" if start else "before line 1"
            inside = [d for d in defs if d.line <= start and d.end_line >= start + 1]
        else:
            where = f"line {start + 1}" if end - start == 1 else f"lines {start + 1}-{end}"
            inside = [d for d in defs if d.line <= start + 1 <= d.end_line]
        if inside:
            d = max(inside, key=lambda d: d.line)  # the innermost
            where += f" (in {d.container + '.' if d.container else ''}{d.name})"
        out.append(where)
    return out


def partly(whole: Planned, chosen: set[int], change_id: int, comment: str) -> str:
    """What the model reads after a write allowed in part (17.2): what went, where the rest is, what the user said -
    and that it reads the file before changing it again."""
    total = len(hunks(whole))
    said = f" The user said: {comment}." if comment else ""
    return (f"{whole.path}: {len(chosen)} of {total} hunks applied, as the user chose (change {change_id}; the user "
            f"can undo it); left out - the file keeps its own text there: {'; '.join(left_out(whole, chosen))}.{said} "
            "The file is not what you proposed: read it again before you change it.")  # fmt: skip


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
