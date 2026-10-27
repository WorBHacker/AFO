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

# code the hub's symbol index reads (hub/symbols.py, SUFFIXES): such a file has an outline
OUTLINE_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx", ".go", ".rs", ".java", ".cs", ".c",
    ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx",
}  # fmt: skip
SYMBOL_KINDS = {
    "function": ("ƒ", "функция"), "method": ("ƒ", "метод"), "class": ("C", "класс"), "interface": ("I", "интерфейс"),
    "type": ("T", "тип"), "module": ("N", "модуль"), "constant": ("K", "константа"), "macro": ("#", "макрос"),
    "impl": ("I", "реализация"),
}  # fmt: skip
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
            "secret": bool(e.get("secret")),
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
    numbers made the same way (padded to one width, so a monospace column lines up on the right). Windows line ends
    are shown as plain ones, so a line's offset in the text is its offset on screen."""
    text = text.replace("\r\n", "\n")
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
        "outline": "." in path.rsplit("/", 1)[-1] and "." + path.rsplit(".", 1)[-1].lower() in OUTLINE_SUFFIXES,
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


def ago(iso: str, now: datetime | None = None) -> str:
    """'только что', '5 мин назад', '3 ч назад', '2 дн назад', or the date for older; the text as is if not a date."""
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    now = now or datetime.now(UTC)
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{int(seconds // 60)} мин назад"
    if seconds < 86400:
        return f"{int(seconds // 3600)} ч назад"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)} дн назад"
    return when.astimezone().strftime("%d.%m.%Y")


def local_time(iso: str) -> str:
    """'27.10.2026 05:03' in this computer's time zone (the hub keeps UTC); the text as is if not a date."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso


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


def highlighted(text: str, ranges: list[list[int]]) -> str:
    """A line as rich text with its matches marked (the selection colour, as an editor marks found text)."""
    p = palette.current()
    out, at = [], 0
    for start, end in sorted(ranges):
        if start < at or end > len(text):
            continue
        out.append(html.escape(text[at:start], quote=False))
        out.append(f'<span style="background-color:{p.selection}; color:{p.text};">'
                   f"{html.escape(text[start:end], quote=False)}</span>")  # fmt: skip
        at = end
    out.append(html.escape(text[at:], quote=False))
    return "".join(out).replace("  ", "&nbsp; ")


def _symbol(symbol: dict[str, Any]) -> dict[str, Any]:
    badge, kind = SYMBOL_KINDS.get(symbol["kind"], ("·", symbol["kind"]))
    return {"badge": badge, "kindText": kind, "tip": f"{kind}: {symbol['signature']}"}


def symbol_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Definitions the symbol index found, a row each: badge, name, its class, file and line."""
    return [
        {
            "kind": "symbol",
            "key": f"s:{s['path']}:{s['line']}:{s['name']}",
            "path": s["path"],
            "name": s["name"],
            "folder": f"{s['path']}:{s['line']}",
            "container": s["container"],
            "count": 0,
            "line": s["line"],
            "html": "",
            **_symbol(s),
        }
        for s in result.get("symbols", [])
    ]


def outline_rows(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A file's definitions in order, nested by their containers."""
    return [
        {
            "key": f"{s['line']}:{s['name']}",
            "name": s["name"],
            "line": s["line"],
            "depth": s["container"].count(".") + 1 if s["container"] else 0,
            **_symbol(s),
        }
        for s in symbols
    ]


def search_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The hub's search answer as one flat list: a row per file, then a row per matching line."""
    if "symbols" in result:
        return symbol_rows(result)
    rows: list[dict[str, Any]] = []
    for item in result.get("files", []):
        path = item["path"]
        rows.append(
            {
                "kind": "file",
                "key": f"f:{path}",
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "folder": path.rsplit("/", 1)[0] if "/" in path else "",
                "count": len(item["matches"]),
                "line": 0,
                "html": "",
            }
        )
        rows += [
            {
                "kind": "match",
                "key": f"m:{path}:{m['line']}",
                "path": path,
                "name": "",
                "folder": "",
                "count": 0,
                "line": m["line"],
                "html": highlighted(*_lead(m["text"].strip(), _stripped(m["text"], m["ranges"]))),
            }
            for m in item["matches"]
        ]
    return rows


LEAD = 8  # characters kept before the first match: the sidebar is narrow, the match must show


def _stripped(text: str, ranges: list[list[int]]) -> list[list[int]]:
    shift = len(text) - len(text.lstrip())
    return [[a - shift, b - shift] for a, b in ranges if a >= shift]


def _lead(text: str, ranges: list[list[int]]) -> tuple[str, list[list[int]]]:
    """A line cut so that it starts a little before its first match."""
    first = min((a for a, _ in ranges), default=0)
    if first <= LEAD + 4:
        return text, ranges
    cut = first - LEAD
    return "…" + text[cut:], [[a - cut + 1, b - cut + 1] for a, b in ranges if a >= cut]


def card_changes(changes: dict[str, Any] | None) -> str:
    """What changed since the card was confirmed, in a line - or "" when nothing worth saying (D24)."""
    if not changes or not changes.get("significant"):
        return ""
    said = []
    commits = changes.get("commits") or 0
    if commits:
        said.append(f"{commits} {presenter.plural(commits, 'коммит', 'коммита', 'коммитов')}")
    if changes.get("dependencies"):
        said.append("изменились " + ", ".join(changes["dependencies"]))
    if changes.get("new_folders"):
        said.append("новые папки: " + ", ".join(f"{f}/" for f in changes["new_folders"]))
    if changes.get("gone_folders"):
        said.append("исчезли папки: " + ", ".join(f"{f}/" for f in changes["gone_folders"]))
    return "С подтверждения карточки: " + "; ".join(said)


def _sections(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    title = ""
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            title = line[3:].strip()
            found.setdefault(title, "")
        else:
            found[title] = found.get(title, "") + line.strip() + "\n"
    return {k: v.strip() for k, v in found.items()}


def changed_sections(before: str, after: str) -> list[str]:
    """The ## sections of `after` that differ from `before` (new ones too, and those gone, marked)."""
    old, new = _sections(before), _sections(after)
    changed = [title or "(начало)" for title, body in new.items() if old.get(title) != body]
    return changed + [f"{title} (убран)" for title in old if title and title not in new]


def search_summary(result: dict[str, Any] | None) -> str:
    if result is None:
        return ""
    if "symbols" in result:
        total, shown = result.get("total", 0), len(result["symbols"])
        text = (
            f"{total} {presenter.plural(total, 'определение', 'определения', 'определений')}"
            + (" (показаны первые)" if total > shown else "")
            if total
            else "определений не найдено"
        )
        return text + ("" if result.get("complete", True) else " · индекс ещё строится")
    count, files = result.get("matches", 0), len(result.get("files", []))
    if not count:
        return "ничего не найдено"
    text = (
        f"{count} {presenter.plural(count, 'совпадение', 'совпадения', 'совпадений')} в {files} "
        f"{presenter.plural(files, 'файле', 'файлах', 'файлах')}"
    )
    return text + (" (показаны первые)" if result.get("truncated") else "")


def line_offset(text: str, line: int) -> int:
    """Where line `line` (from 1) starts in `text`, in characters."""
    at = 0
    for _ in range(max(0, line - 1)):
        found = text.find("\n", at)
        if found < 0:
            return at
        at = found + 1
    return at
