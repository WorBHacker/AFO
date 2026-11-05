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

from common import invisible
from common.i18n import N_, tr

from .. import palette, presenter
from ..render import highlight_lines

# code the hub's symbol index reads (hub/symbols.py, SUFFIXES): such a file has an outline
OUTLINE_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx", ".go", ".rs", ".java", ".cs", ".c",
    ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx",
}  # fmt: skip
SYMBOL_KINDS = {
    "function": ("ƒ", N_("функция")), "method": ("ƒ", N_("метод")), "class": ("C", N_("класс")),
    "interface": ("I", N_("интерфейс")), "type": ("T", N_("тип")), "module": ("N", N_("модуль")),
    "constant": ("K", N_("константа")), "macro": ("#", N_("макрос")), "impl": ("I", N_("реализация")),
}  # fmt: skip
HIGHLIGHT_MAX_BYTES = 300_000  # bigger files are shown as plain text: colouring them takes ~1 s per MB
CHUNK_LINES = 300  # the viewer lays out only the pieces on screen and near it: a long file opens like a short one
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
        return tr("{size} Б", size=size)
    if size < 1024 * 1024:
        return tr("{size:.1f} КБ", size=size / 1024)
    return tr("{size:.1f} МБ", size=size / 1024 / 1024)


def language_of(path: str) -> str:
    try:
        return get_lexer_for_filename(path).aliases[0]
    except ClassNotFound, IndexError:
        return ""


TAB_COLUMNS = 12  # a tab as Qt draws it (80 px) is ~10 monospace columns at 13 px: more is only extra room


def columns_of(text: str) -> int:
    """The longest line in monospace columns: the viewer's width without asking Qt to lay the text out for it."""
    return max((len(line.expandtabs(TAB_COLUMNS)) for line in text.split("\n")), default=0)


def pieces(rows: list[str], coloured: list[str] | None, digits: int) -> list[dict[str, Any]]:
    """Lines in pieces of CHUNK_LINES for the viewer: each piece's code (rich text when coloured), its line numbers
    (plain, padded to `digits`, so a monospace column lines up on the right; none when 0) and where it starts in the
    whole text."""
    chunks, start = [], 0
    for first in range(0, len(rows), CHUNK_LINES):
        plain = "\n".join(rows[first : first + CHUNK_LINES])
        code = plain
        if coloured is not None:
            code = f'<pre style="margin:0; font-family:{palette.MONO_CSS};">'
            code += "\n".join(coloured[first : first + CHUNK_LINES]) + "</pre>"
        last = min(first + CHUNK_LINES, len(rows))
        numbers = "\n".join(str(n).rjust(digits) for n in range(first + 1, last + 1)) if digits else ""
        chunks.append({"code": code, "numbers": numbers, "start": start})
        start += len(plain) + 1
    return chunks


def code_view(text: str, path: str, *, lang: str | None = None) -> dict[str, Any]:
    """A file for the viewer, in pieces (`pieces`): coloured as rich text when not too big, else plain; line numbers
    as plain text (a plain line is as tall as a line of the coloured <pre>, and plain numbers lay out ~5x faster).
    Windows line ends are shown as plain ones, so a line's offset in the text is its offset on screen."""
    text = text.replace("\r\n", "\n")
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    lang = lang if lang is not None else language_of(path)
    rich = bool(lang) and len(text.encode("utf-8", "replace")) <= HIGHLIGHT_MAX_BYTES
    digits = len(str(max(lines, 1)))
    body = text[:-1] if text.endswith("\n") else text
    coloured = highlight_lines(body, lang) if rich else None
    chunks = pieces(body.split("\n"), coloured, digits)
    return {"chunks": chunks, "lines": lines, "rich": rich, "columns": columns_of(text), "digits": digits}


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
        return {"chunks": pieces(body.split("\n"), None, 0), "rich": False, "columns": columns_of(body)}
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
    return {"chunks": pieces(body.split("\n"), out, 0), "rich": True, "columns": columns_of(body)}


def file_view(data: dict[str, Any]) -> dict[str, Any]:
    """The hub's file answer as the viewer shows it (`chunks` go to the viewer apart from the rest: see
    Projects.fileShown)."""
    path = data["path"]
    view: dict[str, Any] = {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "folder": path.rsplit("/", 1)[0] if "/" in path else "",
        "sizeText": size_text(data["size"]),
        "encoding": data.get("encoding", ""),
        "eol": data.get("eol", ""),  # CRLF, LF or both (D46)
        "hidden": invisible.summary(data.get("text", "")),  # invisible characters in it (D46)
        "binary": bool(data.get("binary")),
        "truncated": bool(data.get("truncated")),
        "lang": language_of(path),
        "outline": "." in path.rsplit("/", 1)[-1] and "." + path.rsplit(".", 1)[-1].lower() in OUTLINE_SUFFIXES,
    }
    if not view["binary"]:
        view.update(code_view(data.get("text", ""), path))
        lines = view["lines"]
        view["linesText"] = f"{lines} {presenter.plural(lines, 'строка', 'строки', 'строк')}" if lines else ""
    return view


def git_summary(git: dict[str, Any]) -> str:
    """'main · ↑1 ↓2 · 3 изменения' for the title bar."""
    if not git or not git.get("repo"):
        return tr("не git-репозиторий")
    parts = [git.get("branch") or tr("без ветки")]
    if git.get("ahead") or git.get("behind"):
        parts.append(f"↑{git.get('ahead', 0)} ↓{git.get('behind', 0)}")
    count = len(git.get("changes", []))
    parts.append(
        f"{count} {presenter.plural(count, 'изменение', 'изменения', 'изменений')}" if count else tr("без изменений")
    )
    return " · ".join(parts)


def change_rows(changes: list[dict[str, str]]) -> list[dict[str, str]]:
    labels = {"M": tr("изменён"), "A": tr("добавлен"), "D": tr("удалён"), "R": tr("переименован"), "U": tr("новый"),
              "!": tr("конфликт")}  # fmt: skip
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
        return tr("только что")
    if seconds < 3600:
        return tr("{minutes} мин назад", minutes=int(seconds // 60))
    if seconds < 86400:
        return tr("{hours} ч назад", hours=int(seconds // 3600))
    if seconds < 7 * 86400:
        return tr("{days} дн назад", days=int(seconds // 86400))
    return when.astimezone().strftime("%d.%m.%Y")


TOOL_WORDS = {
    "edit_file": N_("правка"),
    "write_file": N_("запись"),
    "save": N_("сохранено"),
}  # save: the user's own (D47)
RULE_TOOLS = {
    "edit_file": N_("править файлы"),
    "write_file": N_("записывать файлы"),
    "Edit": N_("Claude Code: править файлы"),
    "Write": N_("Claude Code: записывать файлы"),
    "MultiEdit": N_("Claude Code: править файлы"),
}


def write_rows(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A project's writes for the overview - the models' and the user's own saves: path, what, which chat or «вы»,
    when, undone or not."""
    return [{
        "id": c["id"],
        "path": c["path"],
        "note": " · ".join(s for s in (
            tr("создан") if c.get("before_hash") is None else tr(TOOL_WORDS.get(c.get("tool", ""), c.get("tool", ""))),
            tr("чат «{chat}»", chat=c["chat"]) if c.get("chat") else tr("вы") if c.get("tool") == "save" else "",
            local_time(c.get("created_at", "")),
        ) if s),
        "reverted": bool(c.get("reverted_at")),
    } for c in found]  # fmt: skip


def rule_text(key: str) -> str:
    """An "always for this project" answer in words: 'править файлы', 'команда «pytest -q»'."""
    tool, _, command = key.partition(":")
    if command:
        return tr("команда «{command}»", command=command)
    return tr(RULE_TOOLS.get(tool, tool))


EXPORT_MODES = {
    "create": N_("создать файл"),
    "append": N_("добавить блок в конец файла"),
    "replace": N_("обновить блок AFO в файле"),
    "unchanged": N_("уже как в карточке"),
}


def export_question(target: dict[str, Any]) -> str:
    """What the user agrees to when the card goes into a project file (D23)."""
    path = target["path"]
    what = {
        "create": N_("В проекте появится {path} с карточкой, заметками для моделей и строкой о MCP хаба."),
        "append": N_("В конец {path} добавится блок AFO с карточкой; остальное в файле не изменится."),
        "replace": N_("Блок AFO в {path} заменится нынешней карточкой; остальное в файле не изменится."),
    }.get(target["mode"], "")
    said = tr(what, path=path) if what else ""
    return said + tr(" Файл увидит git — его можно закоммитить или удалить; прежний вариант хаб хранит у себя.")


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
    return tr("{size:.1f} ГБ", size=mb / 1024) if mb >= 1024 else tr("{size:.0f} МБ", size=mb)


def age_text(started_at: str, now: datetime) -> str:
    """'5 мин' since the process started; '' when unknown."""
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    minutes = max(0, int((now - started).total_seconds() // 60))
    if minutes < 1:
        return tr("только что")
    if minutes < 60:
        return tr("{minutes} мин", minutes=minutes)
    if minutes < 48 * 60:
        return tr("{hours} ч {minutes} мин", hours=minutes // 60, minutes=minutes % 60)
    return tr("{days} дн", days=minutes // (24 * 60))


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
    kind = tr(kind)
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


def card_changes(changes: dict[str, Any] | None, sections: list[str] | None = None) -> str:
    """What changed since the card was confirmed, in a line - or "" when nothing worth saying (D24); `sections`: the
    template's newer ones the card lacks (18.2) - an update writes them."""
    lacks = tr("нет раздела {names}", names=", ".join(f"«{s}»" for s in sections)) if sections else ""
    if not changes or not changes.get("significant"):
        return tr("В карточке {lacks}", lacks=lacks) if lacks else ""
    said = []
    commits = changes.get("commits") or 0
    if commits:
        said.append(f"{commits} {presenter.plural(commits, 'коммит', 'коммита', 'коммитов')}")
    if changes.get("dependencies"):
        said.append(tr("изменились {names}", names=", ".join(changes["dependencies"])))
    if changes.get("new_folders"):
        said.append(tr("новые папки: {folders}", folders=", ".join(f"{f}/" for f in changes["new_folders"])))
    if changes.get("gone_folders"):
        said.append(tr("исчезли папки: {folders}", folders=", ".join(f"{f}/" for f in changes["gone_folders"])))
    if lacks:
        said.append(tr("в карточке {lacks}", lacks=lacks))
    return tr("С подтверждения карточки: {changes}", changes="; ".join(said))


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
    changed = [title or tr("(начало)") for title, body in new.items() if old.get(title) != body]
    return changed + [tr("{title} (убран)", title=title) for title in old if title and title not in new]


def search_summary(result: dict[str, Any] | None) -> str:
    if result is None:
        return ""
    if "symbols" in result:
        total, shown = result.get("total", 0), len(result["symbols"])
        text = (
            f"{total} {presenter.plural(total, 'определение', 'определения', 'определений')}"
            + (tr(" (показаны первые)") if total > shown else "")
            if total
            else tr("определений не найдено")
        )
        if result.get("left_out"):
            return text + tr(
                " · в индексе не все файлы: {count} не прочитаны, проект слишком большой", count=result["left_out"]
            )
        return text + ("" if result.get("complete", True) else tr(" · индекс ещё строится"))
    count, files = result.get("matches", 0), len(result.get("files", []))
    if not count:
        return tr("ничего не найдено")
    text = tr(
        "{count} {matches} в {files} {in_files}",
        count=count,
        matches=presenter.plural(count, "совпадение", "совпадения", "совпадений"),
        files=files,
        in_files=presenter.plural(files, "файле", "файлах", "файлах"),
    )
    return text + (tr(" (показаны первые)") if result.get("truncated") else "")


def line_offset(text: str, line: int) -> int:
    """Where line `line` (from 1) starts in `text`, in characters."""
    at = 0
    for _ in range(max(0, line - 1)):
        found = text.find("\n", at)
        if found < 0:
            return at
        at = found + 1
    return at
