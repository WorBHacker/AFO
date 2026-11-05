"""The approval queue (D17, D18): an agent asks before it acts, and waits until the user answers.

Claude Code started by the hub asks through the hub's MCP tool approval_prompt (its --permission-prompt-tool); the
request lands here, in the same queue the hub's own agents will use (stage 5). The user answers in the app:
allow once, allow for this chat, allow always for this project (a rule, stage 5), or deny with a comment the
agent reads. An answer "for this chat" or "for this project" covers later requests of the same kind there - the same
tool, or for a command the same command - except destructive commands (git push, reset --hard, rm -r...), which are
asked every time (D17). Each request is a row in `approvals` with who asked, what, and who answered how; nothing is
forgotten. The hub's own models ask here too, before they write a file (write_file, edit_file). A write into a file
the window's editor holds with unsaved edits (`Unsaved`, D47) is asked in every mode, and no answer covers it.
Such a write's request shows its hunks too (17.2, D51), and the user may allow only some of them - this once.

Events: approval.requested {approval}, approval.decided {approval} - both in the feed. A request still open when
its run ends (or the hub stops) is closed as expired: nobody waits for that answer any more.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common import invisible
from common.i18n import N_, tr

from . import changes, projects
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
CLASS_WORDS = {"read": N_("только читает"), "write": N_("меняет данные"), "command": N_("запускает команды"),
               "network": N_("ходит в сеть"), "send": N_("отправляет от вашего имени"),
               "destructive": N_("удаляет или необратимо меняет")}  # fmt: skip
SCOPES = ("once", "chat", "project")
PREVIEW_LINES = 400
PREVIEW_CHARS = 40_000
READ_MAX_BYTES = 2 * 1024 * 1024
REST = r"[^;&|\n]*"  # the rest of the same command, up to the next one
# git and the options it takes before its command (`git -C ../x push` is a push all the same); flags whose letter's
# case matters (-D, -f, -d) are matched with it, the rest of a command without
GIT = (r"\bgit(?:\.exe)?\"?(?:\s+(?:-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)|-c\s+\S+|--(?:git-dir|work-tree|namespace|"
       r"exec-path|config-env)(?:=\S+|\s+\S+)|-p|-P|--no-pager|--paginate|--bare|--no-replace-objects|"
       r"--(?:literal|glob|noglob|icase)-pathspecs|--no-optional-locks|--no-lazy-fetch))*\s+")  # fmt: skip
DESTRUCTIVE = re.compile(
    rf"{GIT}(?:push\b|reset(?={REST}\s--hard\b)|clean(?={REST}\s(?:-[a-z]*f|--force\b))"
    rf"|branch(?:(?={REST}\s(?-i:-[a-zA-Z]*D))|(?={REST}\s(?:(?-i:-d)|--delete)\b)(?={REST}\s(?:(?-i:-f)|--force)\b))"
    rf"|checkout(?={REST}\s(?:--(?:\s|$)|(?-i:-f)\b|--force\b|\.(?:\s|$|[;&|])))"
    rf"|switch(?={REST}\s(?:(?-i:-f)\b|--force\b|--discard-changes\b))|restore\b|stash\s+(?:drop|clear)\b"
    rf"|worktree\s+remove(?={REST}\s(?:(?-i:-f)\b|--force\b))|update-ref(?={REST}\s(?:(?-i:-d)\b|--delete\b))"
    rf"|reflog\s+(?:expire|delete)\b|gc(?={REST}\s--prune\b)|filter-(?:branch|repo)\b)"
    # deleting folders and many files: sh, cmd and PowerShell (its parameters may be cut short: -r, -rec...)
    r"|\brm\s+-\w*[rf]|\b(?:rmdir|rd)\s+/s|\bdel\s+/[sq]"
    rf"|\b(?:Remove-Item|ri|rm|del|erase|rd|rmdir)\b(?={REST}\s-r(?:e(?:c(?:u(?:r(?:se?)?)?)?)?)?(?:\s|$|:))"
    rf"|\b(?:Remove-Item|ri|rm|del|erase)\b(?={REST}\*)|\bClear-Content\b|\bclc\b"
    r"|\bshutil\.rmtree\b|\brimraf\b|\brmSync\b(?=[^\n]*recursive)"
    # disks, the registry, backups, the computer itself, databases
    r"|\bformat\s+[a-z]:|\b(?:Format-Volume|Clear-Disk|Initialize-Disk|Remove-Partition|diskpart)\b"
    rf"|\breg(?:\.exe)?\s+delete\b|\bRemove-ItemProperty\b|\bRemove-Item\b(?={REST}\bHK(?:LM|CU|CR|U|CC):)"
    rf"|\b(?:vssadmin|wbadmin)\b(?={REST}\bdelete\b)|\bbcdedit\b|\bcipher\b(?={REST}\s/w)"
    r"|\bdrop\s+(?:table|database)\b|\btruncate\s+table\b|\b(?:shutdown|Stop-Computer|Restart-Computer|mkfs)\b"
    # what the person cannot read before it runs: an encoded PowerShell command, a string run as code
    r"|\b(?:powershell|pwsh)(?:\.exe)?\b(?=[^\n]*\s-(?:e|ec|en\w*)\s+[A-Za-z0-9+/=]{8,})"
    r"|\b(?:iex|Invoke-Expression)\b",
    re.IGNORECASE,
)
# commands that reach the network - installs, downloads, git's remote work, tools run from a registry: asked every
# time (D19)
NETWORK = re.compile(
    r"\b(?:pip3?|uv|poetry|pipx)\s+(?:install|add|sync|download)\b|\b(?:npm|pnpm)\s+(?:i|install|add|ci|exec|dlx)\b"
    r"|\byarn\s+(?:add|dlx)\b|\b(?:npx|uvx|bunx)\b|\bcurl\b|\bwget\b|\bInvoke-(?:WebRequest|RestMethod)\b|\biwr\b"
    r"|\birm\b|\bNet\.(?:WebClient|Sockets)\b|\bDownload(?:String|File|Data)\b|\bStart-BitsTransfer\b|\bbitsadmin\b"
    rf"|\bcertutil\b(?={REST}-urlcache)|{GIT}(?:clone|pull|fetch|ls-remote|submodule\s+update)\b"
    r"|\bdocker\s+(?:pull|push)\b|\bwinget\b|\bchoco\b|\bscoop\b|\bcargo\s+(?:install|add)\b"
    r"|\bgo\s+(?:get|install)\b|\bssh\b|\bscp\b",
    re.IGNORECASE,
)
# what runs something the line does not name (D46, step 13.11): a string made into code, a new alias or function,
# git's aliases, a command named by a variable, decoded text run - nobody reads what it does before it runs, so it is
# asked as a destructive command is. A computed name after `&` or `.` is CALL's
OPAQUE = re.compile(
    r"\[scriptblock\]::Create|\bNewScriptBlock\b|\.Invoke(?:Script|ReturnAsIs)?\s*\("
    r"|\b(?:Set-Alias|New-Alias)\b|\b(?:sal|nal)\s+(?:-\w+\s+)?[\w-]+\s+[\w.\\/:-]+|\balias\s+[\w-]+="
    r"|\b(?:function|filter)\s+[\w-]+\s*(?:\([^)]*\)\s*)?\{|\bfunction:|\b[\w-]+\s*\(\)\s*\{|\beval\s+[\"'$]"
    r"|(?:^|[;&|]\s*)[\"']?\$\{?\w+\}?[\"']?\s+\w"  # sh: `$g push` (not PowerShell's `foreach ($f in ...)`)
    r"|\bgit\b[^;&|\n]*\balias\.|\bGIT_CONFIG_(?:PARAMETERS|COUNT|KEY_\d+)\b"
    r"|(?=[^\n]*\b(?:exec|eval|system|popen|subprocess|spawn\w*|execSync|child_process|Start-Process)\b)[^\n]*"
    r"(?:b64decode|base64|fromhex|unhexlify|decompress|\batob\b|\bchr\s*\(|fromCharCode|\[char\])",
    re.IGNORECASE,
)
# PowerShell's call operator and dot-sourcing, and what they call: `& "C:\...\git.exe"` names its program, `& $x`
# and `& ('g'+'it')` do not
CALL = re.compile(r"""(?:(?<![&\w])&(?!&)|(?:^|(?<=[;|({&"']))\s*\.(?=\s))\s*(?P<target>"[^"]*"|'[^']*'|[^\s;|&]+)""")
NAME = re.compile(r"[\w.\-]+")
# a variable the line gives a literal and goes on to use: PowerShell's, sh's, cmd's
SET = (
    re.compile(r"\$(\w+)\s*=\s*(?:'([^']*)'|\"([^\"$]*)\")"),
    re.compile(r"(?:^|[;&|]\s*)(\w+)=(?:'([^']*)'|\"([^\"$]*)\"|([^\s'\"$;&|]*))(?=\s*(?:[;&|]|$))"),
    re.compile(r"\bset\s+\"?(\w+)=([^\"&|\n]*)", re.IGNORECASE),
)
USED = re.compile(r"\$\{?(\w+)\b\}?(?!\s*=)|%(\w+)%")
JOINED = re.compile(r"['\"]\s*\+\s*['\"]")  # 'g'+'it'
HANDED = re.compile(r"\s-(?:FileP\w*|PSPath|Ar\w*)\b", re.IGNORECASE)  # Start-Process -FilePath git -ArgumentList
# the hub's own data named in a command (D46, step 13.12): a token file, the database, the names that lead to the data
# folder; the folder's own path is projects.HUB_DATA
HUB_DATA_NAMES = re.compile(
    r"(?<![\w.$])(?:app|hub-[\w-]+)\.token\b(?!\s*\()|\*\.token\b|\btokens/+[^\s\"']*\.token\b|\bhub\.db\b"
    r"|\bLOCALCHAT_(?:DATA_DIR|HOME)\b|\b(?:AFO|LocalChat)/+data\b",
    re.IGNORECASE,
)
SEARCHES = {"web_search", "WebSearch"}  # a query leaves this computer: asked every time, never covered (D19)
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
    hunks: tuple[int, ...] | None = None  # a write allowed in part (17.2): the hunks chosen; None - the whole


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


def spellings(command: str) -> tuple[str, str, str]:
    """A command line as written, and as the shell would read it (D46, step 13.11): without PowerShell's backticks and
    cmd's carets, joined strings joined, the variables the line set put in; and that without quotes and brackets, an
    argument list as arguments - `g`i`t p`u`s`h`, `$g='git'; & $g push` and `Start-Process git -ArgumentList push` are
    pushes. Only for classing: the command runs as written. A reading of text, not a parser."""
    plain = JOINED.sub("", command.replace("`", "").replace("^", ""))
    known: dict[str, str] = {}
    for pattern in SET:
        for match in pattern.finditer(plain):
            known[match.group(1).lower()] = next(g for g in match.groups()[1:] if g is not None)
    plain = USED.sub(lambda m: known.get((m.group(1) or m.group(2)).lower(), m.group(0)), plain)
    bare = HANDED.sub(" ", re.sub(r"@\(|[(),]", " ", re.sub(r"[\"']", "", plain)))
    return command, plain, bare


def _unnamed(target: str) -> bool:
    """What `&` or `.` calls is computed, not named: `$x`, `(...)`, `"$x"`. `& "C:\\...\\git.exe"` and
    `& $env:ProgramFiles\\Git\\bin\\git.exe` name their program; a script block shows what it runs."""
    if target[0] in "([":
        return True
    if target[0] in "\"'":
        if len(target) < 2 or target[-1] != target[0]:
            return False  # a quote mark in text, not a quoted name
        target = target[1:-1]
    elif target[0] != "$":
        return False
    return not NAME.fullmatch(re.split(r"[\\/]", target)[-1])


def opaque(tool: str, data: dict[str, Any]) -> bool:
    """A command that runs something its line does not name (D46, step 13.11): asked as a destructive one, every
    time; the card says why."""
    if tool not in COMMAND_TOOLS:
        return False
    plain = spellings(str(data.get("command", "")))[1]
    return bool(OPAQUE.search(plain)) or any(_unnamed(m.group("target")) for m in CALL.finditer(plain))


def destructive(tool: str, data: dict[str, Any]) -> bool:
    if CLASSED.get(tool) == "destructive":
        return True
    if tool not in COMMAND_TOOLS:
        return False
    found = any(DESTRUCTIVE.search(s) for s in spellings(str(data.get("command", ""))))
    return found or opaque(tool, data)


def hub_data(tool: str, data: dict[str, Any], root: Path | None = None) -> bool:
    """A command that names the hub's own data - its folder, a token file, the database (D46, step 13.12): what it
    prints goes to a model, so it is asked every time, in a trusted chat too, and an answer covers nothing. In a
    project that holds the data folder (a checkout of LocalChat keeps it as `data`), the folder's path inside the
    project names it as well; `root` - the project the command runs in."""
    if tool not in COMMAND_TOOLS:
        return False
    folder = projects.HUB_DATA
    inside = projects.private_part(root) if root is not None else ""
    for text in spellings(str(data.get("command", ""))):
        flat = text.replace("\\", "/")
        if HUB_DATA_NAMES.search(flat):
            return True
        if folder is not None and folder.as_posix().casefold() in flat.casefold():
            return True
        if inside and re.search(rf"(?<![\w.-]){re.escape(inside)}", flat, re.IGNORECASE):
            return True
    return False


def trusted_skips(tool: str, data: dict[str, Any], root: Path | None = None) -> bool:
    """What a trusted chat (D17) does without asking: writes, commands, the network, sending - the user's word
    2026-10-28 - all but a destructive action, which is asked every time and allowed by its button only (D38), and a
    command that reaches into the hub's own data (D46)."""
    return not destructive(tool, data) and not hub_data(tool, data, root)


def written(tool: str, data: dict[str, Any], root: Path | None) -> Path | None:
    """The file a write names: the hub's tools count their path from the project, Claude Code's is absolute or counts
    from it too; None for what is not a write, or a relative path with no project to count from."""
    if tool not in WRITE_TOOLS:
        return None
    name = data.get("path" if tool in HUB_WRITES else "notebook_path" if tool == "NotebookEdit" else "file_path")
    if not isinstance(name, str) or not name.strip():
        return None
    path = Path(name.strip())
    if not path.is_absolute():
        if root is None:
            return None
        path = root / path
    return path


class Unsaved:
    """The files the window's editor holds with edits not saved yet (D47, step 14.2c) - one Ctrl+S from being
    written over: a model's write into one is asked in every mode, a trusted chat's too, and no answer covers it. The
    window says the whole list each time it changes and whenever its events connect; the hub forgets it a while after
    the last window went (api/events_ws.py)."""

    def __init__(self) -> None:
        self._paths: set[str] = set()

    def say(self, paths: list[Path]) -> None:
        self._paths = {found for p in paths if (found := _same(p))}

    def forget(self) -> None:
        self._paths = set()

    def holds(self, tool: str, data: dict[str, Any], root: Path | None) -> bool:
        path = written(tool, data, root)
        return path is not None and _same(path) in self._paths


def _same(path: Path) -> str:
    """One spelling for one file: resolved, in Windows' case ('' for a path that does not resolve)."""
    try:
        return os.path.normcase(path.resolve())
    except OSError, ValueError:
        return ""


def network(tool: str, data: dict[str, Any]) -> bool:
    if CLASSED.get(tool) in ("network", "send") or tool == "mail_send" or tool in SEARCHES:
        return True
    return tool in COMMAND_TOOLS and any(NETWORK.search(s) for s in spellings(str(data.get("command", ""))))


def grant_key(tool: str, data: dict[str, Any], root: Path | None = None) -> str | None:
    """What an answer "for this chat" covers; None when nothing may be covered so (a destructive command, one that
    reaches the network or the hub's own data)."""
    if destructive(tool, data) or network(tool, data) or sends(tool) or hub_data(tool, data, root):
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


def preview(tool: str, data: dict[str, Any], root: Path | None) -> dict[str, Any]:
    """What the user should see to decide: a command, a diff of the file as it would become, a URL, or the input.
    Characters a person cannot see (bidi, zero-width - «Trojan Source», D46) are shown as ⟦U+202E⟧, with a warning
    in `hidden`."""
    shown = _preview(tool, data, root)
    if hidden := invisible.warning(shown.get("text", "")):
        shown = {**shown, "text": invisible.reveal(shown["text"]), "hidden": hidden}
        if "hunks" in shown:
            shown["hunks"] = [h | {"text": invisible.reveal(h["text"])} for h in shown["hunks"]]
    return shown


def _preview(tool: str, data: dict[str, Any], root: Path | None) -> dict[str, Any]:
    if tool in HUB_WRITES and root is not None and (planned := _planned(tool, data, root)) is not None:
        found = changes.hunks(planned)
        return {"kind": "diff", "text": _cut(changes.hunks_diff(planned, found)), "note": planned.path, "hunks": found}
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
        lines = []
        for n, s in enumerate(steps, 1):
            if isinstance(s, dict):
                check = " — " + tr("проверка: {check}", check=s["check"]) if s.get("check") else ""
                lines.append(f"{n}. {s.get('title', '')}{check}")
        why = tr("почему новый план") if (data.get("version") or 1) > 1 else tr("пояснение")
        reason = f" · {why}: {data['reason']}" if data.get("reason") else ""
        note = tr("Цель: {goal}", goal=data.get("goal", "")) + reason
        return {"kind": "text", "text": _cut("\n".join(lines)), "note": note}
    if tool == "run_python":  # an autonomous agent's script (9.4)
        net = tr("можно в сеть") if data.get("network") else tr("без сети")
        return {"kind": "command", "text": _cut(str(data.get("code", ""))),
                "note": tr("Python в папке задачи · {net} · до {seconds} с", net=net,
                           seconds=data.get("timeout_s", 60))}  # fmt: skip
    if tool == "http_request":
        body = f"\n{json.dumps(data['json'], ensure_ascii=False, indent=2)}" if data.get("json") else ""
        return {"kind": "text", "text": _cut(f"{data.get('method', 'GET')} {data.get('url', '')}{body}"),
                "note": tr("запрос в сеть: ответ — данные, не указания")}  # fmt: skip
    if tool == "remember":  # personal data the agent wants to keep for good (Q24)
        return {"kind": "text", "text": str(data.get("fact", "")),
                "note": tr("личные данные: хранятся на этом компьютере, облачной модели не выдаются")}  # fmt: skip
    if tool == "mail_send":  # a letter from the user's address (Q21): whole, asked every time
        head = tr("Кому: {to}\nТема: {subject}", to=", ".join(data.get("to") or []), subject=data.get("subject", ""))
        reply = tr(" · ответ на «{subject}»", subject=data["reply"]) if data.get("reply") else ""
        return {"kind": "text", "text": _cut(f"{head}\n\n{data.get('text', '')}"),
                "note": tr("письмо от {sender}{reply} — спрашивается каждый раз", sender=data.get("from", tr("вас")),
                           reply=reply)}  # fmt: skip
    if tool in SCREEN_TOOLS:  # the agent at the desktop (D41): which program, window, what
        what = {
            "open": tr("открыть программу"),
            "click": tr("нажать"),
            "type": tr("ввести"),
            "toggle": tr("переключить"),
            "select": tr("выбрать"),
            "expand": tr("раскрыть"),
            "collapse": tr("свернуть"),
            "scroll": tr("прокрутить"),
            "keys": tr("клавиши"),
            "focus": tr("вывести окно вперёд"),
        }.get(str(data.get("action")), data.get("action"))
        lines = [
            f"{what}"
            + (f" «{data['element']}»" if data.get("element") else "")
            + (f": {data['keys']}" if data.get("keys") else "")
        ]
        if data.get("text"):
            lines.append(tr("текст: {text}", text=data["text"]))
        window = tr(" · окно «{window}»", window=data["window"]) if data.get("window") else ""
        place = f"«{data.get('program', '')}»{window}"
        note = (
            tr("отправка от вашего имени — спрашивается каждый раз")
            if tool == "screen_send"
            else tr("управление компьютером: «для этой задачи» — в этой программе")
        )
        return {"kind": "text", "text": _cut("\n".join(lines)), "note": f"{place} · {note}"}
    if tool == "start_step":
        return {"kind": "text", "text": f"{data.get('step')}. {data.get('title', '')}",
                "note": tr("проверка: {check}", check=data.get("check")) if data.get("check") else ""}  # fmt: skip
    if tool.startswith("mcp__") and "__" in tool[5:]:  # an outside MCP server's tool (9.5): what it gets, its class
        server = tool[5:].partition("__")[0]
        word = CLASS_WORDS.get(CLASSED.get(tool, ""), "")
        return {"kind": "text", "text": _cut(json.dumps(data, ensure_ascii=False, indent=2)),
                "note": tr("MCP-сервер «{server}»", server=server) + (f" · {tr(word)}" if word else "")}  # fmt: skip
    return {"kind": "text", "text": _cut(json.dumps(data, ensure_ascii=False, indent=2)), "note": ""}


def _planned(tool: str, data: dict[str, Any], root: Path) -> changes.Planned | None:
    """The hub's own write as it would be done (17.2): the diff and its hunks from the very bytes the write would
    put - so the hunks the user chooses are the ones it rebuilds the file from. None when it cannot be worked out
    (the file changed meanwhile, say): the card shows the request as it was asked."""
    try:
        return changes.plan_at(root, tool, data)
    except changes.WriteError, OSError:
        return None


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
    if root is not None and path.is_relative_to(root):
        shown = path.relative_to(root).as_posix()
    elif path.is_absolute():  # no project to count from (an outside client): its folder and name, not a whole
        shown = "/".join(path.parts[-2:])  # C:/Users/... line cut off in the card (2026-10-28)
    else:
        shown = name
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
        text = "".join(lines[:PREVIEW_LINES]) + tr("… ещё {count} строк", count=len(lines) - PREVIEW_LINES) + "\n"
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "\n" + tr("… (обрезано)") + "\n"


def _row(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["input"])
    return dict(row) | {"input": data, "hunks": json.loads(row["hunks"]) if row["hunks"] else None,
                        "kind": kind(row["tool"]), "destructive": destructive(row["tool"], data),
                        "opaque": opaque(row["tool"], data), "network": network(row["tool"], data),
                        "class": CLASSED.get(row["tool"], "")}  # fmt: skip


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
        self.unsaved = Unsaved()  # the window's editor's (PUT /v1/editor/dirty)

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
        chat", or a rule of the project, covers it (unless `cover` is off: a chat that read the web, D19; nor over
        the user's unsaved edits, D47)."""
        unsaved = self.unsaved.holds(tool, data, root)
        key = grant_key(tool, data, root) if cover and not unsaved else None
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

        row = _row(await self._db.acall(write)) | {"preview": shown, "hub_data": hub_data(tool, data, root),
                                                   "unsaved": unsaved}  # fmt: skip
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

    async def decide(self, approval_id: str, *, allow: bool, scope: str = "once", comment: str = "", by: str,
                     hunks: list[int] | None = None) -> dict:  # fmt: skip
        """The user's answer; `hunks` - a write allowed in part (17.2, D51): some hunks of its diff, this once."""
        if allow and scope not in SCOPES:
            raise ApprovalError(f"unknown scope {scope!r}: {', '.join(SCOPES)}")
        chosen = None if hunks is None else await self._chosen(approval_id, allow, scope, hunks)
        return await self._close(approval_id, "allowed" if allow else "denied", scope if allow else None,
                                 comment.strip(), by, chosen)  # fmt: skip

    async def _chosen(self, approval_id: str, allow: bool, scope: str, hunks: list[int]) -> list[int] | None:
        """The hunks of a write the user allows (17.2): at least one, each of them there; all of them - the whole."""
        if not allow:
            raise ApprovalError("hunks are chosen when allowing; to refuse all of it, deny")
        if scope != "once":
            raise ApprovalError("a part of a write is allowed this once only")
        shown = (await self.get(approval_id))["preview"].get("hunks")
        if not shown:
            raise ApprovalError("this request has no hunks to choose")
        chosen = sorted(set(hunks))
        if not chosen:
            raise ApprovalError("no hunk chosen: to refuse all of it, deny")
        if outside := [n for n in chosen if not 0 <= n < len(shown)]:
            raise ApprovalError(f"this write has {len(shown)} hunks, numbered from 0: there is no hunk {outside[0]}")
        return None if len(chosen) == len(shown) else chosen

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
        return row | {"preview": await asyncio.to_thread(preview, row["tool"], row["input"], root),
                      "hub_data": hub_data(row["tool"], row["input"], root),
                      "unsaved": self.unsaved.holds(row["tool"], row["input"], root)}  # fmt: skip

    async def _close(self, approval_id: str, status: str, scope: str | None, comment: str, by: str,
                     hunks: list[int] | None = None) -> dict:  # fmt: skip
        part = json.dumps(hunks) if hunks else None

        def write(conn: sqlite3.Connection) -> sqlite3.Row | None:
            # a destructive command has no grant key, a request without a project no project: an answer "for the
            # chat" or "for the project" then allows this once only (D17)
            updated = conn.execute(
                "UPDATE approvals SET status = ?, scope = CASE WHEN ? IN ('chat', 'project') AND (grant_key IS NULL"
                " OR (? = 'project' AND project_id IS NULL)) THEN 'once' ELSE ? END, comment = ?, hunks = ?,"
                " decided_at = ?, decided_by = ? WHERE id = ? AND status = 'pending'",
                (status, scope, scope, scope, comment or None, part, utc_now(), by, approval_id),
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
            future.set_result(Decision(allowed, comment if allowed else comment or DENIED, approval_id,
                                       tuple(hunks) if hunks else None))  # fmt: skip
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
