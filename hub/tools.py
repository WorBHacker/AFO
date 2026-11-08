"""Tools the hub gives a model in a chat (stage 3, D22 L3): reading the projects attached to the chat.

All of them only read (D17: reading needs no question, within the project, without secrets): a project's files and
folders, search, git, a terminal's recent output, the processes with their ports. A model reaches only the projects
attached to its chat, and a cloud model only those that allow the cloud (D10, D27). Secret files are never read
for it and secrets in any text are masked (hub/secret_filter.py). Answers come in portions (D22): a file 300 lines
at a time, search up to 50 matches, a terminal its last 100 lines; anything longer is cut with a note.

A tool's input is model output, so it is checked before anything runs: a wrong type or an unknown project gives
an error result the model can read and correct, never an exception.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from common import invisible

from . import projects, search
from .limits import DEFAULTS, limit
from .projects import Project, ProjectError
from .secret_filter import clean
from .symbols import SymbolIndex, language_of

MAX_OUTPUT_CHARS = 16_000
FILE_LINES = DEFAULTS["file_lines"]  # a model may have its own (hub/limits.py)
FILE_LINES_MAX = 400
SEARCH_MATCHES = DEFAULTS["search_matches"]  # a model may have its own (hub/limits.py)
TERMINAL_LINES = DEFAULTS["terminal_lines"]  # a model may have its own (hub/limits.py)
DIFF_LINES = 400
LIST_ENTRIES = 300
SYMBOL_MATCHES = 30
SYMBOL_KINDS = ("function", "method", "class", "interface", "type", "module", "constant", "macro", "impl")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>78DEHM]")

PROJECT = {"type": "string", "description": "The project's name, as listed in the system prompt (the chat's only one "
           "when left out; in a chat with none, the hub asks the user which)."}  # fmt: skip
ANY_PROJECT = {"type": "string", "description": "The project's name, as listed in the system prompt; leave it out to "
               "look through all of this chat's projects."}  # fmt: skip

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "List a folder of a project: its subfolders and files with sizes. Follows .gitignore. "
        "Use it to find your way before reading files.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "path": {"type": "string", "description": "A folder inside the project; empty for its root."},
                "depth": {"type": "integer", "description": "How many levels to show, 1 to 3 (default 1)."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "read_file",
        "description": f"Read a text file of a project, {FILE_LINES} lines at a time, with line numbers. For a "
        "longer file call again with start_line. Files with secrets (.env, keys) cannot be read.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "path": {"type": "string", "description": "The file, relative to the project's root."},
                "start_line": {"type": "integer", "description": "The first line to read (default 1)."},
                "line_count": {
                    "type": "integer",
                    "description": f"How many lines (default {FILE_LINES}, at most {FILE_LINES_MAX}).",
                },
            },
            "required": ["project", "path"],
        },
    },
    {
        "name": "search_code",
        "description": f"Search a project's files - or all of this chat's projects' - for text (or a regular "
        f"expression); up to {SEARCH_MATCHES} matching lines with their file and line number. Case is ignored unless "
        "the query has capitals. To find an idea rather than a name (where are rights checked? how are errors "
        "handled?), search once for several words the code would use - in English, as names are written - with "
        "regex true: permission|authoriz|forbidden|is_admin.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": ANY_PROJECT,
                "query": {"type": "string", "description": "What to look for."},
                "regex": {"type": "boolean", "description": "Treat the query as a regular expression (a|b|c)."},
                "glob": {"type": "string", "description": "Only files matching this, e.g. *.py (optional)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "git_status",
        "description": "A project's git state: branch, commits ahead / behind, changed files, recent commits.",
        "parameters": {"type": "object", "properties": {"project": PROJECT}, "required": ["project"]},
    },
    {
        "name": "git_diff",
        "description": f"The uncommitted changes of a project (or of one file in it) as a unified diff, up to "
        f"{DIFF_LINES} lines. `staged`: what is staged for the next commit instead; `base`: what the current branch "
        "changed since it left that branch (e.g. main) - for a review.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "path": {"type": "string", "description": "One file (optional; all changes without it)."},
                "staged": {"type": "boolean", "description": "The staged changes (git diff --cached)."},
                "base": {"type": "string", "description": "A branch: the changes since the branch left it."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "git_log",
        "description": "A project's recent commits: short sha, date, author, subject.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "count": {"type": "integer", "description": "Up to 100; 20 if left out."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "terminal_output",
        "description": f"Without `terminal`: the project's terminals (running and recent). With it: the last "
        f"{TERMINAL_LINES} lines that terminal printed.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "terminal": {"type": "string", "description": "A terminal's id or title (optional)."},
            },
            "required": ["project"],
        },
    },
    {
        "name": "list_processes",
        "description": "Processes running in a project's folder: command, listening ports, CPU, memory.",
        "parameters": {"type": "object", "properties": {"project": PROJECT}, "required": ["project"]},
    },
    # the symbol index (D26); conversations from before it keep the seven above (migration 010)
    {
        "name": "find_symbol",
        "description": "Find where a function, class, method, interface or type is defined in a project (or in all "
        "of this chat's projects), by name: "
        "exact matches first, then names that start with it or contain it; Class.method finds a method of that "
        f"class. Up to {SYMBOL_MATCHES} definitions with file, lines, kind and first line. Quicker and more exact "
        "than search_code for finding a definition. Several English words of what it does (check user permission) "
        "find definitions whose name, comments or docstring say them, best first, with what matched. Knows Python, "
        "JavaScript, TypeScript, Go, Rust, Java, C#, C, C++.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": ANY_PROJECT,
                "name": {"type": "string", "description": "The name, a part of it, or words of what it does."},
                "kind": {
                    "type": "string",
                    "description": f"Only definitions of this kind (optional): {', '.join(SYMBOL_KINDS)}.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "file_outline",
        "description": "The outline of a code file: its classes, functions, methods and other definitions with their "
        "lines, nested as in the file. Look at it before reading a long file, then read only the lines you need.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "path": {"type": "string", "description": "The file, relative to the project's root."},
            },
            "required": ["project", "path"],
        },
    },
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}
# what conversations got before find_symbol and file_outline (migration 010 marks them 'v1')
FIRST_TOOLS = ("list_files", "read_file", "search_code", "git_status", "git_diff", "terminal_output", "list_processes")


class ToolError(Exception):
    """Wrong input or a refusal; the message goes back to the model."""


@dataclass
class ToolContext:
    """What a chat's tools may reach: its projects (by name and id), and the hub's live parts."""

    projects: list[Project]
    terminals: Any = None  # hub.terminals.TerminalManager
    processes: Any = None  # hub.processes.ProcessMonitor
    symbols: SymbolIndex = field(default_factory=SymbolIndex)  # the hub's shared index
    # the chat's: "<project id>:<path>" -> sha256 of the version its model read - write_file over a file changed since
    # (or never read) is refused (D46); None where nobody keeps it (MCP)
    seen: dict[str, str] | None = None
    by_name: dict[str, Project] = field(init=False)

    def __post_init__(self) -> None:
        self.by_name = {p.name.casefold(): p for p in self.projects} | {p.id: p for p in self.projects}

    def add(self, *projects: Project) -> None:
        """Projects attached while the answer goes on (31.2: the user picked one when the model asked for files)."""
        self.projects.extend(projects)
        self.__post_init__()

    def project(self, value: Any) -> Project:
        """The project a call names; none named - the chat's only one (31.1: a model left it out and lost a step)."""
        if not isinstance(value, str) or not value.strip():
            if len(self.projects) == 1:
                return self.projects[0]
            names = ", ".join(p.name for p in self.projects) or "none"
            raise ToolError(f"`project` must be a project's name: {names}")
        found = self.by_name.get(value.strip().casefold()) or self.by_name.get(value.strip())
        if found is None:
            names = ", ".join(p.name for p in self.projects) or "none"
            raise ToolError(f"no project {value!r} among those you may read: {names}")
        return found


def _text(value: Any, name: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ToolError(f"`{name}` must be a string")
    return value


def _int(value: Any, name: str, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"`{name}` must be a whole number")
    return max(low, min(value, high))


def _cut(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... cut: {len(text) - limit} more characters; ask for a smaller part]"


async def run_tool(name: str, args: Any, ctx: ToolContext) -> tuple[str, bool]:
    """(output, is_error) of one tool call; the output is what the model reads."""
    if name not in TOOL_NAMES:
        return f"no tool {name!r}", True
    if not isinstance(args, dict):
        return "the tool's input must be a JSON object", True
    try:
        if name == "terminal_output":
            output = await _terminal_output(args, ctx)
        elif name == "list_processes":
            output = await _processes(args, ctx)
        else:
            output = await asyncio.to_thread(_BLOCKING[name], args, ctx)
    except (ToolError, ProjectError) as exc:
        return str(exc), True
    except OSError as exc:
        return f"could not read: {exc.strerror or exc}", True
    return _cut(clean(output)), False


# --- the tools ---


def _list_files(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    start = _text(args.get("path"), "path")
    depth = _int(args.get("depth"), "depth", 1, 1, 3)
    lines: list[str] = []

    def walk(folder: str, level: int) -> None:
        for entry in projects.tree(project.root, folder):
            if len(lines) >= LIST_ENTRIES:
                return
            indent = "  " * level
            if entry["dir"]:
                lines.append(f"{indent}{entry['name']}/")
                if level + 1 < depth:
                    walk(entry["path"], level + 1)
            else:
                note = " (secret: not readable)" if entry.get("secret") else ""
                lines.append(f"{indent}{entry['name']}  {entry['size']} B{note}")

    walk(start, 0)
    head = f"{project.name}/{start}".rstrip("/") + "/"
    more = f"\n[only the first {LIST_ENTRIES} entries]" if len(lines) >= LIST_ENTRIES else ""
    return head + "\n" + ("\n".join(lines) or "(empty)") + more


def _read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    path = _text(args.get("path"), "path", required=True)
    if projects.secret_file(project.root, path):
        raise ToolError(f"{path} holds secrets (keys, passwords); its content is not shown")
    start = _int(args.get("start_line"), "start_line", 1, 1, 10_000_000)
    count = _int(args.get("line_count"), "line_count", limit("file_lines"), 1, max(FILE_LINES_MAX,
                                                                                    limit("file_lines")))  # fmt: skip
    data = projects.read_file(project.root, path)
    if data["binary"]:
        return f"{data['path']} is a binary file ({data['size']} bytes)"
    if ctx.seen is not None and data["sha256"]:  # the version this chat's model has read (D46)
        ctx.seen[f"{project.id}:{data['path']}"] = data["sha256"]
    lines = data["text"].replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if start > max(total, 1):
        raise ToolError(f"{data['path']} has {total} lines; start_line {start} is past its end")
    part = lines[start - 1 : start - 1 + count]
    end = start + len(part) - 1
    width = len(str(end))
    body = "\n".join(f"{n:>{width}}| {line}" for n, line in enumerate(part, start))
    notes = [", file cut at 2 MB" if data["truncated"] else "",
             f", {data['eol']} line ends" if data["eol"] in ("CRLF", "CRLF+LF") else "",
             f", {data['encoding']}" if data["encoding"] not in ("utf-8", "") else ""]  # fmt: skip
    head = f"{data['path']} (lines {start}-{end} of {total}{''.join(notes)})"
    more = f"\n[{total - end} more lines: read on with start_line={end + 1}]" if end < total else ""
    hidden = [n for n in invisible.lines_with("\n".join(part)) if n]  # numbered within the part
    warn = (f"\n[invisible characters (bidi or zero-width) on lines {', '.join(str(start + n - 1) for n in hidden)}: "
            "the code may read differently from what it does - tell the user]") if hidden else ""  # fmt: skip
    return f"{head}\n{body}{more}{warn}"


def _targets(args: dict[str, Any], ctx: ToolContext) -> list[Project]:
    """The project named, or - none named - every project the chat's model may read (D46, step 13.9)."""
    value = args.get("project")
    if value is None or (isinstance(value, str) and not value.strip()):
        if not ctx.projects:
            raise ToolError("this chat has no projects to look through")
        return list(ctx.projects)
    return [ctx.project(value)]


def _shared(groups: list[list[str]], most: int) -> list[list[str]]:
    """At most `most` lines from several projects' lists: an even share for each first, then what is left over in
    order - one big project does not hide the others."""
    share = max(1, most // max(len(groups), 1))
    taken = [rows[:share] for rows in groups]
    room = most - sum(len(rows) for rows in taken)
    for rows, mine in zip(groups, taken, strict=True):
        extra = rows[len(mine) : len(mine) + max(room, 0)]
        mine += extra
        room -= len(extra)
    return taken


def _search(args: dict[str, Any], ctx: ToolContext) -> str:
    targets = _targets(args, ctx)
    query = _text(args.get("query"), "query", required=True)
    regex = args.get("regex", False)
    if not isinstance(regex, bool):
        raise ToolError("`regex` must be true or false")
    glob = _text(args.get("glob"), "glob")
    matches = limit("search_matches")
    many = len(targets) > 1
    groups, hidden, truncated = [], 0, False
    for project in targets:
        result = search.search(project.root, query, regex=regex, glob=glob, limit=matches * 2)
        truncated = truncated or result["truncated"]
        rows: list[str] = []
        own = projects.private_part(project.root)  # the hub's data folder, when this project holds it
        mark = f"[{project.name}] " if many else ""
        for item in result["files"]:
            if own and item["path"].startswith(own):
                continue
            if projects.secret_file(project.root, item["path"]):  # by any of its names (D46)
                hidden += len(item["matches"])
                continue
            rows += [f"{mark}{item['path']}:{m['line']}: {m['text'].strip()}" for m in item["matches"]]
        groups.append(rows[:matches])
    taken = _shared(groups, matches)
    lines = [line for rows in taken for line in rows]
    where = ", ".join(p.name for p in targets)
    if not lines:  # 18.1: an idea in the user's words is seldom in the code - what to try instead
        return (f"no matches for {query!r} in {where} - try the words the code would use (English names), several "
                "at once as a regex: a|b|c")  # fmt: skip
    note = f"\n[{hidden} matches in secret files not shown]" if hidden else ""
    left = sum(len(rows) for rows in groups) > len(lines)
    more = "\n[more matches exist: narrow the query or use glob]" if truncated or left or len(lines) == matches else ""
    head = f"{len(lines)} matches in {len(targets)} projects ({where})" if many else f"{len(lines)} matches in {where}"
    return f"{head}:\n" + "\n".join(lines) + note + more


MEANING_SHOWN = 15  # definitions shown by meaning (18.3): each line carries its why


def _symbol_line(symbol: dict[str, Any], *, path: str = "", indent: str = "") -> str:
    where = f"{path}:" if path else ""
    lines = f"{symbol['line']}" if symbol["end_line"] == symbol["line"] else f"{symbol['line']}-{symbol['end_line']}"
    name = f"{symbol['container']}.{symbol['name']}" if symbol["container"] and path else symbol["name"]
    return f"{indent}{where}{lines} {symbol['kind']} {name}: {symbol['signature']}"


def _find_symbol(args: dict[str, Any], ctx: ToolContext) -> str:
    targets = _targets(args, ctx)
    name = _text(args.get("name"), "name", required=True).strip()
    if not name:
        raise ToolError("`name` must not be empty")
    kind = _text(args.get("kind"), "kind").strip().lower()
    if kind and kind not in SYMBOL_KINDS:
        raise ToolError(f"`kind` must be one of {', '.join(SYMBOL_KINDS)}")
    many = len(targets) > 1
    groups, total, partial, by_meaning = [], 0, "", False
    for project in targets:
        found = ctx.symbols.find(project, name, kind=kind, limit=SYMBOL_MATCHES)
        if found.get("by") == "meaning":  # each line says why too: fewer of them (the measurement: hits are early)
            found["symbols"] = found["symbols"][:MEANING_SHOWN]
        total += found["total"]
        which = f" ({project.name})" if many else ""
        if found.get("left_out"):
            partial += (f"\n[the project{which} has more files than the index reads: {found['left_out']} are left "
                        "out of it; `search` finds text in all of them]")  # fmt: skip
        elif not found["complete"]:
            partial += f"\n[the index of this project{which} is not complete yet: ask again for the rest]"
        mark = f"[{project.name}] " if many else ""
        groups.append([mark + (f"{s['path']} (the file)" if s["kind"] == "file" else _symbol_line(s, path=s["path"]))
                       + (f" — {s['why']}" if s.get("why") else "") for s in found["symbols"]])  # fmt: skip
        by_meaning = found.get("by") == "meaning"
    shown = MEANING_SHOWN if by_meaning else SYMBOL_MATCHES
    lines = [line for rows in _shared(groups, shown) for line in rows]
    where = ", ".join(p.name for p in targets)
    if not lines and by_meaning:
        return f"no definitions about {name!r} in {where} - try other words the code would use (English){partial}"
    if not lines:
        return f"no definitions named like {name!r} in {where}{partial}"
    if by_meaning:  # 18.3: several words - by what the definitions say of themselves
        head = f"{total} definitions about {name!r} in " + (f"{len(targets)} projects ({where})" if many else where)
        head += " - by their names, comments and docstrings, best first"
    else:
        head = f"{total} definitions match {name!r} in " + (f"{len(targets)} projects ({where})" if many else where)
    head += f", the first {len(lines)}:" if total > len(lines) else ":"
    return f"{head}\n" + "\n".join(lines) + partial


def _file_outline(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    path = _text(args.get("path"), "path", required=True).replace("\\", "/").strip("/")
    if projects.secret_file(project.root, path):
        raise ToolError(f"{path} holds secrets (keys, passwords); its content is not shown")
    target = projects.resolve_inside(project.root, path)
    if not target.is_file():
        raise ToolError(f"no file {path} in {project.name}")
    if language_of(path) is None:
        raise ToolError(f"{path} is not code the outline knows (Python, JavaScript, TypeScript, Go, Rust, Java, C#, "
                        "C, C++); read it with read_file")  # fmt: skip
    outline = ctx.symbols.outline(project, path) or []
    if not outline:
        return f"{path}: no definitions found"
    body = "\n".join(
        _symbol_line(s, indent="  " * (s["container"].count(".") + 1 if s["container"] else 0)) for s in outline
    )
    return f"{path} - {len(outline)} definitions (line or lines, kind, name, first line):\n{body}"


def _git_status(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    status = projects.git_status(project.root)
    if not status["repo"]:
        return f"{project.name} is not a git repository"
    lines = [f"branch {status['branch']}, ahead {status['ahead']}, behind {status['behind']}"]
    if status["changes"]:
        lines.append("changes (index, worktree, path):")
        lines += [f"  {c['index']}{c['worktree']} {c['path']}" for c in status["changes"][:200]]
    else:
        lines.append("no uncommitted changes")
    log = projects.git_log(project.root, 5)
    if log:
        lines.append("recent commits:")
        lines += [f"  {c['sha']} {c['subject']} ({c['author']}, {c['when']})" for c in log]
    return "\n".join(lines)


def _git_diff(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    path = _text(args.get("path"), "path")
    staged = args.get("staged", False)
    if not isinstance(staged, bool):
        raise ToolError("`staged` must be true or false")
    base = _text(args.get("base"), "base").strip()
    if base and not re.fullmatch(r"[\w][\w./-]*", base):
        raise ToolError("`base` must be a branch name, such as main")
    if path and projects.secret_file(project.root, path):
        raise ToolError(f"{path} holds secrets; its changes are not shown")
    diff = projects.git_diff(project.root, path, staged=staged, base=base)["diff"]
    if not diff:
        what = f"no changes since {base}" if base else "nothing staged" if staged else "no uncommitted changes"
        return what + (f" in {path}" if path else "")
    # a diff of a secret file is left out whole (its name may hold spaces: up to " b/")
    sections = re.split(r"(?=^diff --git )", diff, flags=re.M)
    named = [(s, re.match(r"diff --git a/(.+?) b/", s)) for s in sections]
    kept = [s for s, m in named if not m or not projects.secret_file(project.root, m.group(1))]
    lines = "".join(kept).splitlines()
    more = f"\n[{len(lines) - DIFF_LINES} more lines: ask for one file at a time]" if len(lines) > DIFF_LINES else ""
    return "\n".join(lines[:DIFF_LINES]) + more


def _git_log(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    count = _int(args.get("count"), "count", 20, 1, 100)
    log = projects.git_log(project.root, count)
    lines = [f"{c['sha']} {c['when'][:10]} {c['author']}: {c['subject']}" for c in log]
    return "\n".join(lines) if lines else f"{project.name}: no commits (or not a git repository)"


async def _terminal_output(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    wanted = _text(args.get("terminal"), "terminal")
    if ctx.terminals is None:
        raise ToolError("terminals are not available")
    found = await ctx.terminals.list(project.id, 20)
    if not wanted:
        if not found:
            return f"{project.name} has no terminals"
        return "\n".join(f"{t['id']}  {t['title']}  [{_state(t)}]  started {t['created_at'][:16].replace('T', ' ')}"
                         for t in found)  # fmt: skip
    terminal = next((t for t in found if wanted in (t["id"], t["title"])), None)
    if terminal is None:
        raise ToolError(f"no terminal {wanted!r} in {project.name}; call without `terminal` to list them")
    output = await ctx.terminals.output(terminal["id"], 64 * 1024)
    lines = plain_terminal_text(output.get("data", "")).splitlines()
    shown = limit("terminal_lines")
    tail = "\n".join(lines[-shown:])
    return f"{terminal['title']} [{terminal['status']}], last {min(len(lines), shown)} lines:\n{tail}"


def _state(terminal: dict[str, Any]) -> str:
    code = terminal["exit_code"]
    return terminal["status"] + ("" if code is None else f", exit {code}")


async def _processes(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    if ctx.processes is None:
        raise ToolError("the process list is not available")
    found = (ctx.processes.latest or await ctx.processes.sample()).get(project.id, [])
    if not found:
        return f"nothing runs in {project.name}'s folder"
    return "\n".join(
        f"{'  ' * p.get('depth', 0)}{p['name']} (pid {p['pid']})"
        f"{' ports ' + ', '.join(map(str, p['ports'])) if p['ports'] else ''}"
        f"  cpu {p['cpu']}%  {p['memory_mb']} MB  {p.get('args', '')}"
        for p in found
    )


def plain_terminal_text(data: str) -> str:
    """A terminal's output as plain lines: escape sequences out, a carriage return rewrites its line."""
    text = ANSI.sub("", data).replace("\r\n", "\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


_BLOCKING = {
    "list_files": _list_files,
    "read_file": _read_file,
    "search_code": _search,
    "git_status": _git_status,
    "git_diff": _git_diff,
    "git_log": _git_log,
    "find_symbol": _find_symbol,
    "file_outline": _file_outline,
}
