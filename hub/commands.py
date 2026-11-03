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
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .limits import limit
from .projects import Project, ProjectError, resolve_inside
from .secret_filter import clean
from .tools import TERMINAL_LINES, ToolError, plain_terminal_text

DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
POLL_S = 0.2
TITLE_CHARS = 48
QUIET_S = 5.0  # this long without new output, with a question on its last line: the command waits for an answer
QUESTION_CHARS = 2048  # the end of the output looked at for it
# what the commands of models, goals and the proving ground run with: git and pip never wait for an answer
NO_QUESTIONS = {
    "GIT_TERMINAL_PROMPT": "0",  # no user name or password asked in the console
    "GCM_INTERACTIVE": "never",  # nor in a window of Git Credential Manager, which that does not stop
    "GIT_PAGER": "cat",  # `git log` in a console opens less, and less waits for a key
    "GIT_EDITOR": "true",  # `git commit` without -m opens an editor; with this it fails at once, saying why
    "PIP_NO_INPUT": "1",
}
ASKS = re.compile(
    r"(?:[\[(](?:y|yes|n|no|д|да|н|нет)(?:\s*[/|,]\s*\w+)+[\])]"  # [Y/n] (yes/no) [Y,N] (Yes/No/All) [Д/н]
    r"|\?\s*\(y\)"  # npx: Ok to proceed? (y)
    r"|\(default is \"?\w*\"?\)"  # PowerShell's confirmation: [Y] Yes  [A] Yes to All ... (default is "Y")
    r"|\b(?:password|passphrase|username|pin|пароль)\b.*:"  # Password for 'https://...':
    r"|\bpress (?:any key|enter|return)\b.*"  # Press any key to continue . . .
    r"|\b(?:are you sure|continue|proceed|overwrite|replace)\b[^?]*\?"
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
    title = "Модель: " + (command if len(command) <= TITLE_CHARS else command[: TITLE_CHARS - 1] + "…")
    done = await execute(terminals, project, command, folder=args.get("path") or "", timeout=timeout, by=by,
                         title=title)  # fmt: skip
    lines = done["lines"]
    count = limit("terminal_lines")
    tail = "\n".join(lines[-count:])
    shown = f"last {min(len(lines), count)} of {len(lines)} lines" if lines else "no output"
    if done["status"] == "running":
        head = (f"still running after {done['timeout']} s in terminal {done['terminal']} ({title}); it goes on - "
                f"read it later with terminal_output. {shown}:")  # fmt: skip
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
    title: str,
) -> dict[str, Any]:  # fmt: skip
    """Runs a command line in a terminal of the hub inside the project (`folder` - a folder in it) and waits for it,
    at most `timeout` seconds: {terminal, status, exit_code, lines (its plain output), timeout, question}. Status:
    as the terminal's (exited...), "running" after the timeout, or "asked" - it waited for an answer (`question`, its
    last line) and was stopped."""
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
    end, quiet_since, question = -1, time.monotonic(), ""
    while info["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(POLL_S)
        info = await terminals.get(info["id"])
        if info["status"] != "running":
            break
        tail = await terminals.output(info["id"], QUESTION_CHARS)
        if tail["end"] != end:
            end, quiet_since = tail["end"], time.monotonic()
        elif time.monotonic() - quiet_since >= QUIET_S:
            last = next((line.strip() for line in reversed(plain_terminal_text(tail["data"]).splitlines())
                         if line.strip()), "")  # fmt: skip
            if asks(last):
                question = last
                await terminals.kill(info["id"])
                break
    output = await terminals.output(info["id"], 256 * 1024)
    lines = plain_terminal_text(output.get("data", "")).rstrip("\n").splitlines()
    return {"terminal": info["id"], "status": "asked" if question else info["status"],
            "exit_code": None if question else info.get("exit_code"), "lines": lines, "timeout": timeout,
            "question": question}  # fmt: skip
