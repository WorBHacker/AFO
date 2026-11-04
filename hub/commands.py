"""Commands the hub's models run (stage 5, D17): run_command, in a terminal of the hub the user sees in the app.

A command is asked in the approval queue every time, unless an answer "for this chat" or the project's rule (its
whitelist: "always in this project" on that exact command) covers it, or the chat is trusted (since 2026-10-28, the
user's word: a trusted chat runs commands without asking). Destructive commands (git push, rm -r...) are asked every
time, a trusted chat too; commands that reach the network (installs, downloads, D19) are never covered by an
answer or a rule. The command runs through the terminal manager -
a pseudo-console like any terminal the user opens, with its log - and the tool waits for it to end (up to a timeout),
then gives the model the exit code and the last lines. A command still running after the timeout (a server) keeps
running; the model reads it later with terminal_output.

Nobody types into a model's terminal (D46, step 13.8): git and pip are told not to ask (NO_QUESTIONS), and a command
that asks anyway - QUIET_S without new output, a question on its last line (`[y/N]`, `Password:`, `Press any key`) -
is stopped then, not at its timeout. The model reads the question and is told to run it so that it does not ask.

The text alone misread both ways (D46, step 13.13): a build that printed "password for tests:" and thought for a few
seconds was stopped, a question the list did not know waited the whole timeout. So the processor time of the command's
processes is the second sign: a command that works through its quiet is not waiting, whatever its last line says, and
is left alone. A command idle for SILENT_S whose last line only looks like some question (ends in `:`, `?` or `>`)
is not stopped - nobody can be sure - but the model hears of it then, not after the timeout: it goes on, and the model
reads it with terminal_output. Idle processes are not proof of a question either: a wait for the network or a disk
looks the same, which is why that last case is not stopped.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import psutil

from common.i18n import tr

from .limits import limit
from .projects import Project, ProjectError, resolve_inside
from .secret_filter import clean
from .tools import TERMINAL_LINES, ToolError, plain_terminal_text

DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
POLL_S = 0.2
TITLE_CHARS = 48
QUIET_S = 5.0  # this long without new output, with a question on its last line: the command waits for an answer
SILENT_S = 15.0  # this long without output or work, a line that may be a question: the model hears of it (run_command)
IDLE_SHARE = 0.02  # its processes used less of one processor than this through the quiet: they wait, not work
QUESTION_CHARS = 2048  # the end of the output looked at for it
MAYBE_ASKS = re.compile(r"[:?>]\s*$")  # how a question the list below does not know may end
# what the commands of models, goals and the proving ground run with: git and pip never wait for an answer
NO_QUESTIONS = {
    "GIT_TERMINAL_PROMPT": "0",  # no user name or password asked in the console
    "GCM_INTERACTIVE": "never",  # nor in a window of Git Credential Manager, which that does not stop
    "GIT_PAGER": "cat",  # `git log` in a console opens less, and less waits for a key
    "GIT_EDITOR": "true",  # `git commit` without -m opens an editor; with this it fails at once, saying why
    "PIP_NO_INPUT": "1",
}
ASKS = re.compile(
    r"(?:[\[(](?:y|yes|n|no|д|да|н|нет)(?:\s*[/|,]\s*\w+)+[\])]"  # [Y/n] (yes/no) [Y,N] (Yes/No/All) [Д/н]; i18n: keep
    r"|\?\s*\(y\)"  # npx: Ok to proceed? (y)
    r"|\(default is \"?\w*\"?\)"  # PowerShell's confirmation: [Y] Yes  [A] Yes to All ... (default is "Y")
    # Password for 'https://...':, Enter passphrase for key '...':, Password (again): - the word, then only whom it is
    # for: "Running password reset tests:" is a log line (D46, step 13.13)
    r"|\b(?:password|passphrase|username|user name|login|pin|пароль|логин|имя пользователя)"
    r"(?:\s+(?:for|of|для)\b.*|\s*\([^)]*\))?\s*:"
    r"|^\s*(?:enter|type|введите|укажите)\b.*:"  # Enter a value:, Введите номер:
    r"|\b(?:press (?:any key|enter|return)|нажмите (?:любую клавишу|enter|ввод))\b.*"  # Press any key to continue...
    r"|\b(?:are you sure|continue|proceed|overwrite|replace|вы уверены|продолжить|перезаписать|заменить|удалить)\b"
    r"[^?]*\?"
    r"|\(END\)|--\s*More\b.*--|^:"  # a pager waiting for a key (less shows a lone colon)
    r")\s*[:?]?\s*$",
    re.IGNORECASE,
)

RUN_COMMAND: dict[str, Any] = {
    "name": "run_command",
    "description": "Run a shell command (PowerShell) in a project's folder, in a terminal of the hub that the user "
    f"sees in the app. The user is asked first, unless the project allows this exact command. Waits up to timeout_s "
    f"seconds (default {DEFAULT_TIMEOUT_S}, at most {MAX_TIMEOUT_S}) and returns the exit code and the last "
    f"{TERMINAL_LINES} lines. A command still running then (a server) keeps running: read it later with "
    "terminal_output. Destructive commands and commands that use the network are asked every time. Nobody types "
    "into it: pass -y / --yes / -Confirm:$false; a command that waits for an answer ([y/N], a password) is stopped.",
    "parameters": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "The project's name, as listed in the system prompt."},
            "command": {"type": "string", "description": "The command line, as typed in PowerShell."},
            "path": {"type": "string", "description": "A folder inside the project to run it in (default: its root)."},
            "timeout_s": {"type": "integer", "description": f"How long to wait for it (default {DEFAULT_TIMEOUT_S})."},
        },
        "required": ["project", "command"],
    },
}


def asks(line: str) -> bool:
    """The last line of a command's output looks like a question waiting for its answer."""
    return bool(ASKS.search(line))


def tree_work(pid: int | None) -> tuple[float, frozenset[int]] | None:
    """What a terminal's shell and everything it started have done so far: their processor time in seconds, and
    which processes they are (one that ended or began is work too). None when it cannot be read."""
    if not pid:
        return None
    try:
        shell = psutil.Process(pid)
        found = [shell, *shell.children(recursive=True)]
    except psutil.Error:
        return None
    total = 0.0
    for process in found:
        try:
            times = process.cpu_times()
        except psutil.Error:
            continue  # it ended just now
        total += times.user + times.system
    return total, frozenset(p.pid for p in found)


def working(before: tuple[float, frozenset[int]] | None, after: tuple[float, frozenset[int]] | None,
            seconds: float) -> bool:  # fmt: skip
    """The command's processes did work between the two looks: used a processor, or started or ended a process.
    Unknown (either look None) is not work: then the text alone decides, as before."""
    if before is None or after is None:
        return False
    return after[1] != before[1] or after[0] - before[0] > IDLE_SHARE * max(seconds, POLL_S)


def command_of(args: dict[str, Any]) -> str:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolError("`command` must be a command line")
    if "\n" in command.strip():
        raise ToolError("one command line at a time (join steps with ; if they belong together)")
    return command.strip()


async def run(terminals: Any, project: Project, args: dict[str, Any], *, by: str) -> str:
    """Runs the command and waits for it (or for the timeout); what the model reads."""
    command = command_of(args)
    timeout = args.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ToolError("`timeout_s` must be a whole number of seconds")
    short = command if len(command) <= TITLE_CHARS else command[: TITLE_CHARS - 1] + "…"
    title = tr("Модель: {command}", command=short)
    done = await execute(terminals, project, command, folder=args.get("path") or "", timeout=timeout, by=by,
                         title=title, early=True)  # fmt: skip
    lines = done["lines"]
    count = limit("terminal_lines")
    tail = "\n".join(lines[-count:])
    shown = f"last {min(len(lines), count)} of {len(lines)} lines" if lines else "no output"
    if done["status"] == "running":
        head = (f"still running after {done['timeout']} s in terminal {done['terminal']} ({title}); it goes on - "
                f"read it later with terminal_output. {shown}:")  # fmt: skip
    elif done["status"] == "silent":
        head = (f"still running in terminal {done['terminal']} ({title}), but silent and idle for {SILENT_S:.0f} s, "
                f"and its last line may be a question ({done['question']!r}) nobody types an answer to. It goes on - "
                "read it with terminal_output. If it waits for an answer, run it so that it does not ask, or ask the "
                f"user. {shown}:")  # fmt: skip
    elif done["status"] == "asked":
        head = (f"stopped: it waited for an answer ({done['question']!r}), and nobody types into this terminal. Run "
                "it so that it does not ask - -y, --yes, --force, -Confirm:$false, a --non-interactive flag, the "
                "password in the tool's own settings - or ask the user to run it in their own terminal. "
                f"{shown}:")  # fmt: skip
    else:
        head = f"exit code {done['exit_code']}; {shown}:"
    return clean(f"$ {command}\n{head}\n{tail}")


async def execute(
    terminals: Any, project: Project, command: str, *, folder: Any = "", timeout: int = DEFAULT_TIMEOUT_S, by: str,
    title: str, early: bool = False,
) -> dict[str, Any]:  # fmt: skip
    """Runs a command line in a terminal of the hub inside the project (`folder` - a folder in it) and waits for it,
    at most `timeout` seconds: {terminal, status, exit_code, lines (its plain output), timeout, question}. Status:
    as the terminal's (exited...), "running" after the timeout, "asked" - it waited for an answer (`question`, its
    last line) and was stopped, or, with `early` (a model's command), "silent" - idle for SILENT_S on a line that may
    be a question: it goes on, the caller hears of it now."""
    if terminals is None:
        raise ToolError("terminals are not available")
    if not isinstance(folder, str):
        raise ToolError("`path` must be a folder inside the project")
    try:
        cwd = resolve_inside(project.root, folder) if folder else project.root
    except ProjectError as exc:
        raise ToolError(str(exc)) from exc
    timeout = max(1, min(timeout, MAX_TIMEOUT_S))
    info = await terminals.create(cwd=cwd, by=by, project_id=project.id, command=command, title=title,
                                  env=NO_QUESTIONS)  # fmt: skip
    deadline = time.monotonic() + timeout
    end, question, silent = -1, "", False
    quiet_since = look_at = time.monotonic()
    work: tuple[float, frozenset[int]] | None = None  # what its processes had done when the quiet began
    looked = False
    while info["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(POLL_S)
        info = await terminals.get(info["id"])
        if info["status"] != "running":
            break
        tail = await terminals.output(info["id"], QUESTION_CHARS)
        now = time.monotonic()
        if tail["end"] != end:  # new output: the quiet starts over
            end, quiet_since, look_at, looked = tail["end"], now, now + QUIET_S, False
            continue
        if not looked:  # the quiet's first moment: what the processes had done by then
            work, looked = await asyncio.to_thread(tree_work, info.get("pid")), True
            continue
        if now < look_at:
            continue
        look_at = now + QUIET_S
        now_work = await asyncio.to_thread(tree_work, info.get("pid"))
        if working(work, now_work, now - quiet_since):  # it works through the quiet: not waiting, whatever it says
            quiet_since, work = now, now_work
            continue
        last = next((line.strip() for line in reversed(plain_terminal_text(tail["data"]).splitlines())
                     if line.strip()), "")  # fmt: skip
        if asks(last):
            question = last
            await terminals.kill(info["id"])
            break
        if early and now - quiet_since >= SILENT_S and MAYBE_ASKS.search(last):
            question, silent = last, True
            break
    output = await terminals.output(info["id"], 256 * 1024)
    lines = plain_terminal_text(output.get("data", "")).rstrip("\n").splitlines()
    return {"terminal": info["id"], "status": "silent" if silent else "asked" if question else info["status"],
            "exit_code": None if question else info.get("exit_code"), "lines": lines, "timeout": timeout,
            "question": question}  # fmt: skip
