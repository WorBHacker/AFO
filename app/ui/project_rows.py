"""Project data -> what the QML view shows: tree rows, git marks, a file with line numbers, summaries. No Qt here.

Git marks follow VS Code: M changed, A added, D deleted, R renamed, U new (untracked), ! conflict; a folder with
changes inside gets a dot.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

from .. import palette, presenter
from ..render import highlight_code

HIGHLIGHT_MAX_BYTES = 300_000  # bigger files are shown as plain text: rich text that large is slow to lay out
CONFLICT = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def mark(change: dict[str, str]) -> str:
    code = change["index"] + change["worktree"]
    if code == "??":
        return "U"
    if code in CONFLICT:
        return "!"
    letter = change["worktree"] if change["worktree"] != " " else change["index"]
    return {"M": "M", "A": "A", "D": "D", "R": "R", "C": "A", "T": "M"}.get(letter, letter)


def marks(changes: list[dict[str, str]]) -> dict[str, str]:
    """path -> mark for changed files, and a dot for every folder that holds one."""
    found: dict[str, str] = {}
    for change in changes:
        path = change["path"].rstrip("/")
        found[path] = mark(change)
        parts = path.split("/")
        for depth in range(1, len(parts)):
            found.setdefault("/".join(parts[:depth]), "•")
    return found


def tree_rows(entries: list[dict[str, Any]], depth: int, changed: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "key": e["path"],
            "name": e["name"],
            "path": e["path"],
            "depth": depth,
            "dir": e["dir"],
            "open": False,
            "status": changed.get(e["path"], ""),
            "size": e.get("size", 0),
        }
        for e in entries
    ]


def size_text(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} КБ"
    return f"{size / 1024 / 1024:.1f} МБ"


def language_of(path: str) -> str:
    try:
        return get_lexer_for_filename(path).aliases[0]
    except ClassNotFound, IndexError:
        return ""


def code_view(text: str, path: str, *, lang: str | None = None) -> dict[str, Any]:
    """A file (or a diff) for the viewer: the code, coloured as rich text when not too big, else plain, and line
    numbers made the same way (padded to one width, so a monospace column lines up on the right)."""
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    lang = lang if lang is not None else language_of(path)
    rich = bool(lang) and len(text.encode("utf-8", "replace")) <= HIGHLIGHT_MAX_BYTES
    width = len(str(max(lines, 1)))
    numbers = "\n".join(str(n).rjust(width) for n in range(1, max(lines, 1) + 1))
    body = text[:-1] if text.endswith("\n") else text
    if rich:
        style = f"margin:0; font-family:{palette.MONO_CSS};"
        body = f'<pre style="{style}">{highlight_code(body, lang)}</pre>'
        numbers = f'<pre style="{style}">{numbers}</pre>'
    return {"code": body, "numbers": numbers, "lines": lines, "rich": rich}


DIFF_META = (
    "diff ",
    "index ",
    "--- ",
    "+++ ",
    "new file",
    "deleted file",
    "similarity",
    "rename ",
    "old mode",
    "new mode",
)


def diff_view(text: str) -> dict[str, Any]:
    """A unified diff line by line in the theme's own colours (code styles colour diffs unevenly): added lines
    green, removed red, hunk headers in the accent, file headers muted."""
    body = text[:-1] if text.endswith("\n") else text
    if len(text.encode("utf-8", "replace")) > HIGHLIGHT_MAX_BYTES:
        return {"code": body, "rich": False}
    p = palette.current()
    out = []
    for line in body.split("\n"):
        if line.startswith(DIFF_META):
            colour = p.muted
        elif line.startswith("@@"):
            colour = p.accent
        elif line.startswith("+"):
            colour = p.ok
        elif line.startswith("-"):
            colour = p.error
        else:
            colour = ""
        escaped = html.escape(line, quote=False)
        out.append(f'<span style="color:{colour};">{escaped}</span>' if colour else escaped)
    return {
        "code": f'<pre style="margin:0; font-family:{palette.MONO_CSS};">' + "\n".join(out) + "</pre>",
        "rich": True,
    }


def file_view(data: dict[str, Any]) -> dict[str, Any]:
    """The hub's file answer as the viewer shows it."""
    path = data["path"]
    view: dict[str, Any] = {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "folder": path.rsplit("/", 1)[0] if "/" in path else "",
        "sizeText": size_text(data["size"]),
        "encoding": data.get("encoding", ""),
        "binary": bool(data.get("binary")),
        "truncated": bool(data.get("truncated")),
        "text": data.get("text", ""),
        "lang": language_of(path),
    }
    if not view["binary"]:
        view.update(code_view(view["text"], path))
        lines = view["lines"]
        view["linesText"] = f"{lines} {presenter.plural(lines, 'строка', 'строки', 'строк')}" if lines else ""
    return view


def git_summary(git: dict[str, Any]) -> str:
    """'main · ↑1 ↓2 · 3 изменения' for the title bar."""
    if not git or not git.get("repo"):
        return "не git-репозиторий"
    parts = [git.get("branch") or "без ветки"]
    if git.get("ahead") or git.get("behind"):
        parts.append(f"↑{git.get('ahead', 0)} ↓{git.get('behind', 0)}")
    count = len(git.get("changes", []))
    parts.append(
        f"{count} {presenter.plural(count, 'изменение', 'изменения', 'изменений')}" if count else "без изменений"
    )
    return " · ".join(parts)


def change_rows(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    labels = {"M": "изменён", "A": "добавлен", "D": "удалён", "R": "переименован", "U": "новый", "!": "конфликт"}
    rows = []
    for change in changes:
        path = change["path"].rstrip("/")
        letter = mark(change)
        rows.append(
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "folder": path.rsplit("/", 1)[0] if "/" in path else "",
                "mark": letter,
                "label": labels.get(letter, letter),
                "deleted": letter == "D",
            }
        )
    return rows


def program_args(cmdline: str, exe: str) -> str:
    """The command line without the program itself (it is shown by name)."""
    line = cmdline.strip()
    for head in (f'"{exe}"', exe):
        if exe and line.lower().startswith(head.lower()):
            return line[len(head) :].strip()
    if line.startswith('"'):
        end = line.find('"', 1)
        return line[end + 1 :].strip() if end > 0 else ""
    return line.partition(" ")[2].strip()


def memory_text(mb: float) -> str:
    return f"{mb / 1024:.1f} ГБ" if mb >= 1024 else f"{mb:.0f} МБ"


def age_text(started_at: str, now: datetime) -> str:
    """'5 мин' since the process started; '' when unknown."""
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    minutes = max(0, int((now - started).total_seconds() // 60))
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 48 * 60:
        return f"{minutes // 60} ч {minutes % 60} мин"
    return f"{minutes // (24 * 60)} дн"


def process_rows(processes: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """A project's processes as the overview shows them (the hub sends them as a tree, parents first)."""
    now = now or datetime.now(UTC)
    return [
        {
            "pid": p["pid"],
            "name": p["name"],
            "args": p["args"] if "args" in p else program_args(p.get("cmdline", ""), p.get("exe", "")),
            "cmdline": p.get("cmdline", ""),
            "folder": p.get("cwd") or "",
            "depth": min(p.get("depth", 0), 6),
            "ports": p.get("ports", []),
            "cpu": f"{p['cpu']:.1f}%" if p.get("cpu", 0) >= 0.1 else "",
            "memory": memory_text(p.get("memory_mb", 0)),
            "age": age_text(p.get("started_at", ""), now),
            "hub": bool(p.get("hub")),
        }
        for p in processes
    ]


def processes_summary(processes: list[dict[str, Any]]) -> str:
    """'3 процесса · порты 8000, 5173' for the overview's heading."""
    count = len(processes)
    if not count:
        return ""
    ports = sorted({port for p in processes for port in p.get("ports", [])})
    text = f"{count} {presenter.plural(count, 'процесс', 'процесса', 'процессов')}"
    if ports:
        text += " · " + presenter.plural(len(ports), "порт ", "порты ", "порты ") + ", ".join(map(str, ports))
    return text
