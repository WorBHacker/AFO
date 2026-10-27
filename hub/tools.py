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

from . import projects, search
from .limits import DEFAULTS, limit
from .projects import Project, ProjectError
from .secret_filter import clean, is_secret_file
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

PROJECT = {"type": "string", "description": "The project's name, as listed in the system prompt."}

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
        "description": f"Search a project's files for text (or a regular expression); up to {SEARCH_MATCHES} "
        "matching lines with their file and line number. Case is ignored unless the query has capitals.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "query": {"type": "string", "description": "What to look for."},
                "regex": {"type": "boolean", "description": "Treat the query as a regular expression."},
                "glob": {"type": "string", "description": "Only files matching this, e.g. *.py (optional)."},
            },
            "required": ["project", "query"],
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
        f"{DIFF_LINES} lines.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "path": {"type": "string", "description": "One file (optional; all changes without it)."},
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
        "description": "Find where a function, class, method, interface or type is defined in a project, by name: "
        "exact matches first, then names that start with it or contain it; Class.method finds a method of that "
        f"class. Up to {SYMBOL_MATCHES} definitions with file, lines, kind and first line. Quicker and more exact "
        "than search_code for finding a definition. Knows Python, JavaScript, TypeScript, Go, Rust, Java, C#, C, "
        "C++.",
        "parameters": {
            "type": "object",
            "properties": {
                "project": PROJECT,
                "name": {"type": "string", "description": "The name, or a part of it."},
                "kind": {
                    "type": "string",
                    "description": f"Only definitions of this kind (optional): {', '.join(SYMBOL_KINDS)}.",
                },
            },
            "required": ["project", "name"],
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
    by_name: dict[str, Project] = field(init=False)

    def __post_init__(self) -> None:
        self.by_name = {p.name.casefold(): p for p in self.projects} | {p.id: p for p in self.projects}

    def project(self, value: Any) -> Project:
        if not isinstance(value, str) or not value.strip():
            raise ToolError("`project` must be a project's name")
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
    if is_secret_file(path):
        raise ToolError(f"{path} holds secrets (keys, passwords); its content is not shown")
    start = _int(args.get("start_line"), "start_line", 1, 1, 10_000_000)
    count = _int(args.get("line_count"), "line_count", limit("file_lines"), 1, max(FILE_LINES_MAX,
                                                                                    limit("file_lines")))  # fmt: skip
    data = projects.read_file(project.root, path)
    if data["binary"]:
        return f"{data['path']} is a binary file ({data['size']} bytes)"
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
    head = f"{data['path']} (lines {start}-{end} of {total}{', file cut at 2 MB' if data['truncated'] else ''})"
    more = f"\n[{total - end} more lines: read on with start_line={end + 1}]" if end < total else ""
    return f"{head}\n{body}{more}"


def _search(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    query = _text(args.get("query"), "query", required=True)
    regex = args.get("regex", False)
    if not isinstance(regex, bool):
        raise ToolError("`regex` must be true or false")
    glob = _text(args.get("glob"), "glob")
    matches = limit("search_matches")
    result = search.search(project.root, query, regex=regex, glob=glob, limit=matches * 2)
    lines, hidden = [], 0
    for item in result["files"]:
        if is_secret_file(item["path"]):
            hidden += len(item["matches"])
            continue
        for match in item["matches"]:
            if len(lines) < matches:
                lines.append(f"{item['path']}:{match['line']}: {match['text'].strip()}")
    if not lines:
        return f"no matches for {query!r} in {project.name}"
    note = f"\n[{hidden} matches in secret files not shown]" if hidden else ""
    more = (
        "\n[more matches exist: narrow the query or use glob]" if result["truncated"] or len(lines) == matches else ""
    )
    return f"{len(lines)} matches in {project.name}:\n" + "\n".join(lines) + note + more


def _symbol_line(symbol: dict[str, Any], *, path: str = "", indent: str = "") -> str:
    where = f"{path}:" if path else ""
    lines = f"{symbol['line']}" if symbol["end_line"] == symbol["line"] else f"{symbol['line']}-{symbol['end_line']}"
    name = f"{symbol['container']}.{symbol['name']}" if symbol["container"] and path else symbol["name"]
    return f"{indent}{where}{lines} {symbol['kind']} {name}: {symbol['signature']}"


def _find_symbol(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    name = _text(args.get("name"), "name", required=True).strip()
    if not name:
        raise ToolError("`name` must not be empty")
    kind = _text(args.get("kind"), "kind").strip().lower()
    if kind and kind not in SYMBOL_KINDS:
        raise ToolError(f"`kind` must be one of {', '.join(SYMBOL_KINDS)}")
    found = ctx.symbols.find(project, name, kind=kind, limit=SYMBOL_MATCHES)
    partial = "" if found["complete"] else "\n[the index of this project is not complete yet: ask again for the rest]"
    if not found["symbols"]:
        return f"no definitions named like {name!r} in {project.name}{partial}"
    head = f"{found['total']} definitions match {name!r} in {project.name}"
    head += f", the first {SYMBOL_MATCHES}:" if found["total"] > SYMBOL_MATCHES else ":"
    body = "\n".join(_symbol_line(s, path=s["path"]) for s in found["symbols"])
    return f"{head}\n{body}{partial}"


def _file_outline(args: dict[str, Any], ctx: ToolContext) -> str:
    project = ctx.project(args.get("project"))
    path = _text(args.get("path"), "path", required=True).replace("\\", "/").strip("/")
    if is_secret_file(path):
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
    if path and is_secret_file(path):
        raise ToolError(f"{path} holds secrets; its changes are not shown")
    diff = projects.git_diff(project.root, path)["diff"]
    if not diff:
        return "no uncommitted changes" + (f" in {path}" if path else "")
    # a diff of a secret file is left out whole
    sections = re.split(r"(?=^diff --git )", diff, flags=re.M)
    kept = [s for s in sections if not (m := re.match(r"diff --git a/(\S+)", s)) or not is_secret_file(m.group(1))]
    lines = "".join(kept).splitlines()
    more = f"\n[{len(lines) - DIFF_LINES} more lines: ask for one file at a time]" if len(lines) > DIFF_LINES else ""
    return "\n".join(lines[:DIFF_LINES]) + more


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
    "find_symbol": _find_symbol,
    "file_outline": _file_outline,
}
