"""Project data -> what the QML view shows: tree rows, git marks, a file with line numbers, summaries. No Qt here.

Git marks follow VS Code: M changed, A added, D deleted, R renamed, U new (untracked), ! conflict; a folder with
changes inside gets a dot.
"""

from __future__ import annotations

import html
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
