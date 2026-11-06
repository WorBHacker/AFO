"""Claude Code under the hub (stage 4, D8, D16, D18): the user's own `claude` CLI, logged in with their subscription,
working in a chat's project.

The hub never logs in for it and never touches its tokens (D8): it starts the official CLI the user already uses.
A chat whose model is `claude-code/<model>` runs each user message as one turn of
    claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
in the chat's project folder; the message goes in on stdin, and `--resume <session>` continues the chat's Claude
Code session. What it streams becomes the hub's own: text and thinking deltas go out as chat.delta, its messages are
stored as the chat's messages (its tool calls and their results included), and its result ends the run with
chat.done. Its usage is recorded as an estimate with no money: the subscription pays (D12).

Claude Code also gets the hub's MCP server (`--mcp-config`, with a token of its own and the chat's id in a header),
so it reads the ecosystem through the same tools as any MCP client, and asks for permissions through the hub
(`--permission-prompt-tool mcp__localchat__approval_prompt`): its requests join the app's approval queue (D18).

The same CLI can run interactively in a hub terminal (`terminal_command`): then it asks in its own window.

A chat with no project (2026-10-28, the user's word) works too: Claude Code starts in an empty folder of that chat's
own (`<data>/claude-code/chats/<chat>`), the user's files elsewhere. The chat's rights apply to what it asks (D17):
«Только чтение» refuses its edits and commands, «Доверенный» lets its edits through without a card; its own questions
(AskUserQuestion) come to the chat with their options to pick (`autonomous.Autonomy.ask_question`).

It may work on a chat's goal (11.7, D44): the hub starts each turn with its note, within `--max-turns`, and hears how
the turn ended; a goal's investigation and its constraints refuse its permission requests (hub/guard.py).

The chat's depth goes as `--effort` (21.2) - the goal's, on a goal's turn - except to a session whose model has no
effort levels; the user's CLAUDE_CODE_EFFORT_LEVEL, which would win over the flag, is then not passed on.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.i18n import tr
from common.paths import DataPaths

from . import projects, store
from .auth import create_client, find_client, rotate_token
from .db import Database
from .events import EventBus
from .gateway import GatewayError, Model, ProviderError
from .gateway.base import reasoning_block, text_block, tool_call_block, tool_result_block
from .processes import stop_tree
from .projects import Project

log = logging.getLogger("hub.claude_code")

PROVIDER = "claude-code"
CLI_ENV = "LOCALCHAT_CLAUDE"  # the CLI to run, when it is not where the hub looks
HUB_CLIENT = "hub-claude-code"  # the client token the hub gives the Claude Code it starts
SERVER = "localchat"  # the hub's MCP server inside Claude Code: its tools are mcp__localchat__*
PERMISSION_TOOL = f"mcp__{SERVER}__approval_prompt"
CONVERSATION_HEADER = "X-LocalChat-Conversation"
MODELS = {"default": "Claude Code", "opus": "Claude Code · Opus", "sonnet": "Claude Code · Sonnet",
          "haiku": "Claude Code · Haiku"}  # fmt: skip
RESULT_MAX_CHARS = 30_000  # of one tool result kept in the chat (the rest stays in Claude Code's own session)
SUB_STEPS = 200  # a subagent's steps kept with the call that started it (21.3)
SUB_TEXT_CHARS = 2_000  # of a subagent's text, or of a result it got
SUB_INPUT_CHARS = 200  # of a value in a subagent call's input
FORWARD_SUBAGENT_TEXT = (2, 1, 211)  # the CLI that knows --forward-subagent-text: a subagent's texts come too
VERSION_TIMEOUT_S = 15
LIMITS_KEY = "claude_code.limits"  # the subscription's limits as last told, per window (20.3)
LIMIT_RETRY_S = 600  # a reached limit with no reset said: tried again after this
EARLIER_CHARS = 6_000  # of the chat before Claude Code joined it, given with its first message
STDERR_LINES = 30
STOP_WAIT_S = 3.0
AFTER_RESULT_S = 10.0  # Claude Code may finish background work after its answer; then the hub stops it
WAIT_MS = 24 * 3600 * 1000  # an MCP call may wait this long: the user answers a permission request when they can
NOT_PASSED = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
              "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_SIMPLE"}  # fmt: skip
EFFORT_ENV = "CLAUDE_CODE_EFFORT_LEVEL"  # wins over --effort: not passed when the hub gives the chat's depth (21.2)
# models with no effort levels (its documentation: Opus 4.6 and later, Sonnet 4.6, Haiku 5.5 and the 5.x models have
# them): Claude 3, the 4.0-4.5 ones and Haiku 4.x - as the CLI names the model of a session
NO_EFFORT = re.compile(r"^claude-(?:\d|(?:opus|sonnet|haiku)-(?:[0-3]|4-[0-5]|4-\d{8})(?:\D|$)|haiku-4)")
APPEND_PROMPT = (
    "You work under AFO, a hub on the user's computer that knows all their projects. Its MCP tools "
    "(mcp__localchat__*) give the map of the projects and their links (get_ecosystem_map), a project's card "
    "confirmed by the user (get_project_card), and other projects' code, git, terminals, processes and recent "
    "events. Use them when the task touches another project or what is running. The user answers your permission "
    "requests in the AFO app; a denial may carry their comment - follow it."
)
NO_PROJECT_PROMPT = (
    " This chat has no project attached: you start in an empty folder of the chat's own, and the user's files are "
    "not there. Ask the user (AskUserQuestion) before you go looking for files elsewhere on their computer."
)


def find_cli(environ: dict[str, str] | None = None) -> list[str] | None:
    """The command that runs the `claude` CLI: $LOCALCHAT_CLAUDE, then PATH, then where its installers put it
    (the native installer, npm, the Claude desktop app's own copy). An npm shim is run through node directly, so
    no cmd.exe stands between the hub and the CLI."""
    env = os.environ if environ is None else environ
    if chosen := env.get(CLI_ENV, "").strip():
        return _runnable(Path(chosen))
    home = Path(env.get("USERPROFILE") or Path.home())
    appdata = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    candidates = [shutil.which("claude"), home / ".local" / "bin" / "claude.exe", appdata / "npm" / "claude.cmd"]
    for found in candidates:
        if found and Path(found).is_file():
            return _runnable(Path(found))
    bundled = _newest_bundled(appdata / "Claude" / "claude-code")
    return [str(bundled)] if bundled else None


def _runnable(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    if path.suffix.lower() in (".cmd", ".bat"):
        script = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node = shutil.which("node")
        if script.is_file() and node:
            return [node, str(script)]
    return [str(path)]


def _newest_bundled(root: Path) -> Path | None:
    """The Claude desktop app keeps the CLI as claude-code/<version>/<build>/claude.exe: the newest version."""

    def version(path: Path) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", path.parent.parent.name))

    found = sorted(root.glob("*/*/claude.exe"), key=version) if root.is_dir() else []
    return found[-1] if found else None


class ClaudeCodeProvider:
    """The gateway's view of Claude Code: which "models" the chat may pick. Its turns run in ClaudeCodeRunner."""

    name = PROVIDER

    def __init__(self, find: Callable[[], list[str] | None] = find_cli):
        self._find = find

    async def list_models(self) -> list[Model]:
        if not await asyncio.to_thread(self._find):
            return []  # no CLI on this computer: no Claude Code in the model list
        capabilities = {"tools": True, "agent": True, "subscription": True}
        return [Model(f"{PROVIDER}/{ref}", PROVIDER, ref, name, "cloud", capabilities, {}) for ref, name in
                MODELS.items()]  # fmt: skip

    def accepts(self, model_ref: str) -> frozenset[str]:
        return frozenset()  # an agent with its own settings: the hub sets none of its parameters

    async def stream_chat(self, *args: Any, **kwargs: Any):
        raise ProviderError("Claude Code works as an agent: the hub runs it with ClaudeCodeRunner")
        yield  # an async generator, as the gateway expects


# --- what Claude Code streams ---


@dataclass
class Step:
    """One thing the hub does with what Claude Code streamed."""

    kind: str  # session | delta | message | sub | limit | result
    data: Any = None
    role: str = ""
    raw: dict[str, Any] | None = None


class Translator:
    """Claude Code's stream-json lines -> the hub's steps: its session, text and thinking deltas, its messages in
    the hub's block format (tool calls, then their results as a message of role "tool"), subscription limit
    notices, and the final result.

    Claude Code sends each finished block of a model message as its own line (they share message.id); the blocks
    of one message are gathered into one stored message - up to its first tool call, so the call shows in the chat
    while it runs (and while it waits for the user's approval).

    A subagent's own messages (parent_tool_use_id: the call that started it, 21.3) are not the chat's: each of its
    texts, calls and their results is a step "sub" (the window shows it under that call as it comes), and all of
    them go with the call's result - its block's `sub` - so the chat keeps what the subagent did. No model gets them
    from the hub: Claude Code's own model has its subagent's answer, the call's result, as before."""

    def __init__(self) -> None:
        self.calls: dict[str, str] = {}  # tool_use id -> tool name
        self._pending: list[dict[str, Any]] = []
        self._pending_id: str | None = None
        self._pending_raw: list[dict[str, Any]] = []
        self._subs: dict[str, list[dict[str, Any]]] = {}  # a subagent's steps, by the call that started it

    def feed(self, event: dict[str, Any]) -> list[Step]:
        kind = event.get("type")
        if parent := event.get("parent_tool_use_id"):
            return self._sub(parent, kind, event.get("message") or {})
        if kind == "stream_event":
            delta = (event.get("event") or {}).get("delta") or {}
            if delta.get("type") == "text_delta":
                return [Step("delta", delta.get("text", ""), role="content")]
            if delta.get("type") == "thinking_delta":
                return [Step("delta", delta.get("thinking", ""), role="reasoning")]
            return []
        if kind == "assistant":
            message = event.get("message") or {}
            steps = self.flush() if message.get("id") != self._pending_id else []
            self._pending_id = message.get("id")
            self._pending += self._assistant(message)
            self._pending_raw.append(event)
            if any(b["type"] == "tool_call" for b in self._pending):
                steps += self.flush()
            return steps
        steps = self.flush()
        if kind == "system" and event.get("subtype") == "init":
            steps.append(Step("session", event))
        elif kind == "user":
            results = self._results(event.get("message") or {})
            if results:
                steps.append(Step("message", results, role="tool", raw=event))
        elif kind == "rate_limit_event":
            steps.append(Step("limit", event.get("rate_limit_info") or {}))
        elif kind == "result":
            steps.append(Step("result", event))
        return steps

    def flush(self) -> list[Step]:
        """The gathered blocks of the model message so far, as one message."""
        if not self._pending:
            self._pending_raw = []
            return []
        step = Step("message", self._pending, role="assistant", raw={"events": self._pending_raw})
        self._pending, self._pending_raw = [], []
        return [step]

    def _assistant(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in message.get("content") or []:
            match block.get("type"):
                case "text" if block.get("text"):
                    blocks.append(text_block(block["text"]))
                case "thinking" if block.get("thinking"):
                    blocks.append(reasoning_block(block["thinking"]))
                case "tool_use":
                    self.calls[block.get("id", "")] = block.get("name", "")
                    blocks.append(tool_call_block(block.get("id", ""), block.get("name", ""), block.get("input", {})))
        return blocks

    def _results(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        content = message.get("content")
        if not isinstance(content, list):
            return []
        results = []
        for block in content:
            if block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id", "")
            output = _text_of(block.get("content"))
            if len(output) > RESULT_MAX_CHARS:
                more = tr("… (ещё {count} символов)", count=len(output) - RESULT_MAX_CHARS)
                output = output[:RESULT_MAX_CHARS] + "\n" + more
            result = tool_result_block(call_id, self.calls.get(call_id, ""), output, bool(block.get("is_error")))
            if sub := self._subs.pop(call_id, None):  # what the subagent this call started did (21.3)
                result["sub"] = sub
            results.append(result)
        return results

    def _sub(self, parent: str, kind: Any, message: dict[str, Any]) -> list[Step]:
        """A subagent's message: its texts, calls and their results as the steps it took (21.3)."""
        found: list[dict[str, Any]] = []
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            match (kind, block.get("type")):
                case ("assistant", "text") if block.get("text", "").strip():
                    found.append({"type": "text", "text": _short(block["text"], SUB_TEXT_CHARS)})
                case ("assistant", "tool_use"):
                    self.calls[block.get("id", "")] = block.get("name", "")
                    found.append({"type": "call", "name": block.get("name", ""), "input": _small(block.get("input"))})
                case ("user", "tool_result"):
                    found.append({"type": "result", "name": self.calls.get(block.get("tool_use_id", ""), ""),
                                  "error": bool(block.get("is_error")),
                                  "output": _short(_text_of(block.get("content")), SUB_TEXT_CHARS)})  # fmt: skip
        kept = self._subs.setdefault(parent, [])
        found = found[: max(SUB_STEPS - len(kept), 0)]
        kept += found
        return [Step("sub", {"parent": parent, "step": step}) for step in found]


def _short(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _small(data: Any) -> dict[str, Any]:
    """A subagent call's input, its long values cut: what it called is kept, not the files it wrote."""
    if not isinstance(data, dict):
        return {}
    return {k: _short(v, SUB_INPUT_CHARS) if isinstance(v, str) else v for k, v in data.items()
            if isinstance(v, str | int | float | bool)}  # fmt: skip


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')}]" for b in content
                         if isinstance(b, dict))  # fmt: skip
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def read_limit(info: dict[str, Any]) -> dict[str, Any]:
    """A rate_limit_event's info as the hub keeps it (20.3): {status, window, used (0-1, None - not said), resets_at
    (seconds since the epoch, None - not said)}. Its documentation spells the fields two ways (resetsAt / resets_at,
    rateLimitType / rate_limit_type) and does not say seconds or milliseconds: both are read."""

    def pick(*names: str) -> Any:
        return next((info[n] for n in names if info.get(n) is not None), None)

    def number(value: Any) -> float | None:
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

    resets = number(pick("resetsAt", "resets_at"))
    if resets is not None:
        resets = resets / 1000 if resets > 10**11 else resets
    used = number(pick("utilization"))
    if used is not None and used > 1:
        used /= 100  # a percentage
    return {"status": str(info.get("status") or ""), "window": str(pick("rateLimitType", "rate_limit_type") or ""),
            "used": used, "resets_at": resets if resets and resets > 0 else None}  # fmt: skip


def usage_of(result: dict[str, Any]) -> dict[str, int]:
    """Claude Code's usage in the hub's terms: input_tokens is the whole prompt, cache reads and writes inside it."""
    usage = result.get("usage") or {}
    read, write = int(usage.get("cache_read_input_tokens") or 0), int(usage.get("cache_creation_input_tokens") or 0)
    return {
        "input_tokens": int(usage.get("input_tokens") or 0) + read + write,
        "cached_input_tokens": read,
        "cache_write_tokens": write,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "estimated": 1,
    }


def user_line(text: str) -> bytes:
    """One user message for `--input-format stream-json`: a single newline-terminated JSON line."""
    message = {"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None}
    return json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"


def child_env(environ: dict[str, str] | None = None, *, effort: bool = False) -> dict[str, str]:
    """Claude Code's environment: the user's, without what would make it bill an API key or another provider
    instead of the subscription (D8: in -p mode an ANTHROPIC_API_KEY in the environment always wins), and with MCP
    time limits that let a permission request wait for the user (by default an HTTP call ends after 60 s, an idle
    one after 5 min, and a long one is moved to the background). `effort`: the hub gives the depth (--effort), so
    the user's CLAUDE_CODE_EFFORT_LEVEL, which would win over it, stays out (21.2)."""
    dropped = NOT_PASSED | ({EFFORT_ENV} if effort else set())
    env = {k: v for k, v in (os.environ if environ is None else environ).items() if k.upper() not in dropped}
    return env | {"MCP_TOOL_TIMEOUT": str(WAIT_MS), "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT": "0",
                  "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "0"}  # fmt: skip


def private_rules(root: Path | None = None) -> list[str]:
    """Claude Code's own file tools stay out of the hub's data folder - its tokens, database, logs (D46, step 13.12):
    they read the project's files without asking anyone, and a checkout of LocalChat holds that folder. Deny rules on
    the folder's absolute path, in Claude Code's POSIX form (D:\\x -> //d/x); a Read rule keeps out Grep and Glob as
    well, an Edit rule the writing tools. Its shell commands come to the hub's approval_prompt, which asks about them
    (approvals.hub_data). Not checked with the real CLI - its documentation's syntax.

    Where it works inside that folder itself (`root`: a copy of a proving ground's task, 20.5; a chat's own folder
    when it has no project) - in Claude Code a deny wins over any allow, so the rules close each entry beside the
    way down to it instead (the database, tokens, logs, the other copies), as the folder holds them at the start."""
    data = projects.HUB_DATA
    if data is None:
        return []
    if root is None or not root.resolve().is_relative_to(data.resolve()) or root.resolve() == data.resolve():
        return _denied(data, folder=True)
    rules, here = [], data.resolve()
    for part in root.resolve().relative_to(data.resolve()).parts:
        try:
            beside = sorted(e for e in here.iterdir() if e.name.casefold() != part.casefold())
        except OSError:
            beside = []
        for entry in beside:
            rules += _denied(entry, folder=entry.is_dir())
        here = here / part
    return rules


def _denied(path: Path, *, folder: bool) -> list[str]:
    path_text = path.as_posix()
    drive, colon, rest = path_text.partition(":")
    where = f"//{drive.lower()}{rest}" if colon and len(drive) == 1 else f"/{path_text}"
    target = f"{where}/**" if folder else where
    return [f"Read({target})", f"Edit({target})"]


def effort_for(effort: str | None, session_model: str | None) -> str | None:
    """The chat's depth for --effort (21.2): None when the session's model, as the CLI named it, has no effort levels.
    A level the model lacks (xhigh on Opus 4.6) the CLI itself takes down to the nearest it has."""
    if not effort or (session_model and NO_EFFORT.match(session_model)):
        return None
    return effort


def error_text(result: dict[str, Any]) -> str:
    """What went wrong, from a result with is_error: its text, or its list of errors, or its kind."""
    errors = result.get("errors")
    text = result.get("result") or ("; ".join(map(str, errors)) if isinstance(errors, list) else "")
    return text or str(result.get("subtype") or "error")


def earlier_text(history: list[store.MessageRow]) -> str:
    """The chat before Claude Code joined it (other models' turns): its visible text, the latest part."""
    lines = []
    for message in history:
        text = "".join(b.get("text", "") for b in message.content if b.get("type") == "text").strip()
        if message.role in ("user", "assistant") and text:
            lines.append(f"{'User' if message.role == 'user' else 'Assistant'}: {text}")
    joined = "\n\n".join(lines)
    return joined[-EARLIER_CHARS:] if joined else ""


# --- running it ---


@dataclass
class Turn:
    """A turn in progress: its process and what the approval tool needs to know about it."""

    conversation_id: str
    project: Project | None  # None: a chat with no project - Claude Code works in `root`, a folder of the chat's own
    root: Path
    process: subprocess.Popen | None = None
    effort: bool = False  # the hub gives the depth (21.2)
    rejected: float | None = None  # the subscription's limit was reached: when it resets (0 - not said), 20.3


class ClaudeCodeRunner:
    def __init__(
        self,
        db: Database,
        bus: EventBus,
        paths: DataPaths,
        hub_url: Callable[[], str],
        find: Callable[[], list[str] | None] = find_cli,
    ):
        self._db = db
        self._bus = bus
        self._paths = paths
        self._hub_url = hub_url
        self._find = find
        self.turns: dict[str, Turn] = {}  # conversation_id -> its turn now running
        self._versions: dict[tuple[str, ...], tuple[int, ...] | None] = {}  # each CLI's version, asked once

    # --- the hub's MCP for Claude Code ---

    def token(self) -> str:
        """The hub's own client for the Claude Code it starts: a cloud client (D32), its token kept in data/tokens."""
        token_file = self._paths.tokens / f"{HUB_CLIENT}.token"

        def ensure(conn: sqlite3.Connection) -> str:
            if token_file.is_file():
                stored = token_file.read_text(encoding="utf-8").strip()
                if stored and find_client(conn, stored):
                    return stored
            exists = conn.execute("SELECT 1 FROM clients WHERE name = ?", (HUB_CLIENT,)).fetchone()
            return rotate_token(conn, HUB_CLIENT) if exists else create_client(conn, HUB_CLIENT, cloud=True)

        token = self._db.call(ensure)
        self._paths.tokens.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
        return token

    def mcp_config(self, name: str, conversation_id: str | None = None) -> Path:
        """An --mcp-config file: the hub's MCP over HTTP, with the token (and the chat, for its approvals)."""
        headers = {"Authorization": f"Bearer {self.token()}"}
        if conversation_id:
            headers[CONVERSATION_HEADER] = conversation_id
        server = {"type": "http", "url": f"{self._hub_url()}/mcp", "headers": headers, "timeout": WAIT_MS}
        config = {"mcpServers": {SERVER: server}}
        folder = self._paths.root / "claude-code"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.mcp.json"
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return path

    def cli(self) -> list[str]:
        found = self._find()
        if not found:
            raise GatewayError(
                "Claude Code is not installed here: the hub found no `claude` CLI (PATH, ~/.local/bin, npm, the "
                f"Claude desktop app). Install it, or set ${CLI_ENV} to its path."
            )
        return found

    def version(self) -> tuple[int, ...] | None:
        """The CLI's version (`claude --version`: «2.1.211 (Claude Code)»), asked once per CLI; None - unknown."""
        cli = tuple(self.cli())
        if cli not in self._versions:
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                done = subprocess.run([*cli, "--version"], capture_output=True, timeout=VERSION_TIMEOUT_S,
                                      env=child_env(), creationflags=flags)  # fmt: skip
                found = re.search(rb"(\d+)\.(\d+)\.(\d+)", done.stdout)
                self._versions[cli] = tuple(int(n) for n in found.groups()) if found else None
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("claude --version did not answer: %s", exc)
                self._versions[cli] = None
        return self._versions[cli]

    def command(self, model_ref: str, config: Path, *, resume: str | None, extra_dirs: list[Path],
                project: bool = True, max_turns: int | None = None, effort: str | None = None,
                root: Path | None = None) -> list[str]:  # fmt: skip
        """The CLI's arguments for a turn in `root` (the hub's data folder closed to its file tools but for it)."""
        prompt = APPEND_PROMPT + ("" if project else NO_PROJECT_PROMPT)
        argv = [*self.cli(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--mcp-config", str(config), "--permission-prompt-tool",
                PERMISSION_TOOL, "--append-system-prompt", prompt]  # fmt: skip
        if model_ref != "default":
            argv += ["--model", model_ref]
        if effort:  # the chat's depth (21.2)
            argv += ["--effort", effort]
        if (self.version() or ()) >= FORWARD_SUBAGENT_TEXT:  # a subagent's texts too, not only its calls (21.3)
            argv.append("--forward-subagent-text")
        if resume:
            argv += ["--resume", resume]
        for folder in extra_dirs:
            argv += ["--add-dir", str(folder)]
        if max_turns:  # a goal's turn (11.7): its tool steps, as the hub's own loop has (print mode only)
            argv += ["--max-turns", str(max_turns)]
        if rules := private_rules(root):
            argv += ["--disallowedTools", *rules]  # last: the flag takes the rules that follow it
        return argv

    def terminal_command(self, project: Project) -> list[str]:
        """Interactive Claude Code for a hub terminal: the hub's tools, its own permission prompts."""
        return [*self.cli(), "--mcp-config", str(self.mcp_config(f"terminal-{project.id}"))]

    # --- one turn ---

    async def run(
        self,
        conversation_id: str,
        model: dict[str, Any],
        text: str,
        projects: list[Project],
        cancel: asyncio.Event,
        client_id: str | None,
        *,
        max_turns: int | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """One turn at the chat's depth (`effort`); how it ended: {usage, error ("" - none), limited (it stopped at
        `max_turns`), cancelled, rejected (the subscription's limit was reached: when it resets, 0 - not said; else
        None)}."""
        project = projects[0] if projects else None
        folder = project.root if project else self.chat_folder(conversation_id)
        session, earlier = await self._db.acall(lambda conn: _session_and_history(conn, conversation_id))
        resume = session.get("id") if session.get("cwd") == str(folder) else None  # a session lives in its folder
        if not resume and earlier:
            text = f"<earlier_in_this_chat>\n{earlier}\n</earlier_in_this_chat>\n\n{text}"
        effort = effort_for(effort, session.get("model"))
        config = await asyncio.to_thread(self.mcp_config, conversation_id, conversation_id)
        argv = await asyncio.to_thread(self.command, model["model_ref"], config, resume=resume,
                                       extra_dirs=[p.root for p in projects[1:]], project=project is not None,
                                       max_turns=max_turns, effort=effort, root=folder)  # fmt: skip
        turn = Turn(conversation_id, project, folder, effort=effort is not None)
        self.turns[conversation_id] = turn
        try:
            return await self._turn(turn, argv, model, text, cancel, client_id)
        finally:
            self.turns.pop(conversation_id, None)
            with contextlib.suppress(OSError):
                config.unlink()

    def chat_folder(self, conversation_id: str) -> Path:
        """Where Claude Code works for a chat with no project: an empty folder of that chat's own."""
        folder = self._paths.root / "claude-code" / "chats" / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    async def _turn(self, turn, argv, model, text, cancel, client_id) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[bytes | None] = asyncio.Queue()
        errors: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)
        try:
            process = await asyncio.to_thread(_start, argv, turn.root, turn.effort)
        except OSError as exc:
            raise GatewayError(f"Claude Code did not start: {exc}") from exc
        turn.process = process
        log.info("claude code turn for %s: pid %s in %s", turn.conversation_id, process.pid, turn.root)
        _pipe_thread(process.stdout, lambda line: loop.call_soon_threadsafe(lines.put_nowait, line))
        _pipe_thread(process.stderr, lambda line: errors.append(line.decode("utf-8", "replace").rstrip()) if line
                     else None)  # fmt: skip
        await asyncio.to_thread(_send, process, user_line(text))

        translator = Translator()
        last_id: int | None = None
        result: dict[str, Any] | None = None
        stopper = asyncio.create_task(cancel.wait())
        try:
            while True:
                reading = asyncio.create_task(lines.get())
                done, _ = await asyncio.wait({reading, stopper}, return_when=asyncio.FIRST_COMPLETED)
                if stopper in done:
                    reading.cancel()
                    await asyncio.to_thread(stop_tree, process.pid, STOP_WAIT_S)
                    break
                line = reading.result()
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    log.warning(
                        "claude code printed a line that is not JSON (%d characters, starting %r)", len(line), line[:80]
                    )  # it may be the conversation's text: its start is enough for the log
                    continue
                for step in translator.feed(event):
                    last_id = await self._apply(turn, model, step) or last_id
                    if step.kind == "result":
                        result = step.data
                if result is not None:  # the answer is complete; what it still does in the background is not
                    break
            for step in translator.flush():  # a message cut off by a stop
                last_id = await self._apply(turn, model, step) or last_id
        finally:
            stopper.cancel()
            await asyncio.to_thread(_wind_down, process)
        ended = await self._finish(turn.conversation_id, model, result, last_id, cancel.is_set(), errors, client_id,
                                   process.returncode)  # fmt: skip
        return {**ended, "rejected": turn.rejected}

    async def _apply(self, turn: Turn, model: dict, step: Step) -> int | None:
        conversation_id = turn.conversation_id
        if step.kind == "session":
            await self._db.acall(lambda conn: _save_session(conn, conversation_id, step.data, turn.root))
        elif step.kind == "delta" and step.data:
            payload = {"conversation_id": conversation_id, "kind": step.role, "text": step.data}
            await self._bus.publish("chat.delta", payload, persist=False)
        elif step.kind == "message":
            model_id = model["id"] if step.role == "assistant" else None

            def write(conn: sqlite3.Connection) -> store.MessageRow:
                return store.add_message(conn, conversation_id, step.role, step.data, model_id=model_id,
                                         provider_raw=step.raw)  # fmt: skip

            message = await self._db.acall(write)
            await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})
            return message.id if step.role == "assistant" else None
        elif step.kind == "sub":  # a subagent's step, under its call in the window (21.3); kept with its result
            await self._bus.publish("chat.subagent", {"conversation_id": conversation_id, **step.data}, persist=False)
        elif step.kind == "limit":
            await self._limit(turn, step.data)
        return None

    # --- the subscription's limits (D12, 20.3) ---

    async def _limit(self, turn: Turn, info: dict[str, Any]) -> None:
        """A limit's state: kept per window (and across restarts), told to the window; near ("allowed_warning") or
        reached ("rejected") - into the feed as well, and a reached one ends the turn as the limit's (the goal waits
        for it to reset instead of failing)."""
        state = read_limit(info) | {"seen_at": time.time()}
        known = await self._db.acall(_saved_limits)
        known[state["window"]] = state
        await self._db.acall(lambda conn: _save_limits(conn, known))
        await self._bus.publish("claude_code.limits", {"limits": current_limits(known)}, persist=False)
        if state["status"] == "rejected":
            turn.rejected = state["resets_at"] or 0.0
        if state["status"] not in ("", "allowed"):
            payload = {"conversation_id": turn.conversation_id, "info": info, "limit": state}
            await self._bus.publish("claude_code.limit", payload, project_id=turn.project.id if turn.project else None)

    async def limits(self) -> list[dict[str, Any]]:
        """The subscription's limits as last told, per window - those not reset yet."""
        return current_limits(await self._db.acall(_saved_limits))

    async def limited_until(self) -> float | None:
        """When a reached limit resets (one not said: LIMIT_RETRY_S after it was told); None - none is reached."""
        found = [s["resets_at"] or s["seen_at"] + LIMIT_RETRY_S for s in await self.limits()
                 if s["status"] == "rejected"]  # fmt: skip
        return max(found) if found else None

    async def _finish(self, conversation_id, model, result, last_id, cancelled, errors, client_id, code) -> dict:
        if result is None and not cancelled:
            detail = "\n".join(errors) or f"Claude Code ended (code {code}) without an answer"
            raise GatewayError(f"Claude Code: {detail[-1500:]}")
        usage = usage_of(result) if result else {}
        stats = {}
        if result:
            stats = {"duration_s": round((result.get("duration_ms") or 0) / 1000, 1), "turns": result.get("num_turns"),
                     "cost_estimate_usd": result.get("total_cost_usd"), "subscription": True}  # fmt: skip
            meta = {"stats": stats, "stop_reason": result.get("subtype"), "session_id": result.get("session_id"),
                    "subscription": True, "model_usage": result.get("modelUsage")}  # fmt: skip
            await self._db.acall(lambda conn: store.record_usage(
                conn, client_id=client_id, conversation_id=conversation_id, message_id=last_id, model_id=model["id"],
                usage=usage, cost_usd=0.0, estimated=True, meta=meta))  # fmt: skip
        limited = bool(result) and result.get("subtype") == "error_max_turns"  # --max-turns: a goal's turn ends
        if result and result.get("is_error") and not cancelled and not limited:
            detail = f"Claude Code: {error_text(result)}"
            payload = {"conversation_id": conversation_id, "detail": detail, "usage": usage, "cost_usd": 0}
            await self._bus.publish("chat.error", payload)
            return {"usage": usage, "error": detail, "limited": False, "cancelled": False}
        await self._bus.publish("chat.done", {
            "conversation_id": conversation_id, "message_id": last_id, "usage": usage, "stats": stats, "cost_usd": 0.0,
            "steps": (result or {}).get("num_turns") or 0, "stop_reason": (result or {}).get("subtype"),
            "cancelled": cancelled,
        })  # fmt: skip
        said = f"Claude Code stopped at its limit of {(result or {}).get('num_turns')} turns" if limited else ""
        return {"usage": usage, "error": said, "limited": limited, "cancelled": cancelled}

    async def stop(self) -> None:
        """The hub stops: so do the turns it started."""
        for turn in list(self.turns.values()):
            if turn.process is not None and turn.process.poll() is None:
                await asyncio.to_thread(stop_tree, turn.process.pid, STOP_WAIT_S)


def _start(argv: list[str], cwd: Path, effort: bool = False) -> subprocess.Popen:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=child_env(effort=effort), creationflags=flags)  # fmt: skip


def _wind_down(process: subprocess.Popen) -> None:
    """After its answer (or a stop) Claude Code gets a little while to end on its own; then it is stopped."""
    try:
        process.wait(AFTER_RESULT_S)
    except subprocess.TimeoutExpired:
        stop_tree(process.pid, STOP_WAIT_S)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(STOP_WAIT_S)


def _send(process: subprocess.Popen, line: bytes) -> None:
    """The message, then the end of input: Claude Code answers it and exits."""
    try:
        process.stdin.write(line)
        process.stdin.close()
    except OSError:  # it ended already; its stderr says why
        pass


def _pipe_thread(pipe, deliver: Callable[[bytes | None], Any]) -> None:
    def pump() -> None:
        try:
            for line in iter(pipe.readline, b""):
                if line.strip():
                    deliver(line)
        finally:
            deliver(None)

    threading.Thread(target=pump, name="claude-code-pipe", daemon=True).start()


def current_limits(known: dict[str, dict[str, Any]], now: float | None = None) -> list[dict[str, Any]]:
    """The windows whose state still holds: not past their reset (one with no reset said - for LIMIT_RETRY_S)."""
    now = time.time() if now is None else now
    return [s for _w, s in sorted(known.items())
            if (s.get("resets_at") or s.get("seen_at", 0) + LIMIT_RETRY_S) > now]  # fmt: skip


def _saved_limits(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (LIMITS_KEY,)).fetchone()
    return json.loads(row["value"]) if row else {}


def _save_limits(conn: sqlite3.Connection, known: dict[str, dict[str, Any]]) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET"
                 " value = excluded.value", (LIMITS_KEY, json.dumps(known)))  # fmt: skip


def _session_and_history(conn: sqlite3.Connection, conversation_id: str) -> tuple[dict, str]:
    row = conn.execute("SELECT agent_session FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    session = json.loads(row["agent_session"]) if row and row["agent_session"] else {}
    history = store.get_messages(conn, conversation_id)[:-1]  # without the message being sent now
    return session, earlier_text([m for m in history if not (m.model_id or "").startswith(f"{PROVIDER}/")])


def _save_session(conn: sqlite3.Connection, conversation_id: str, init: dict[str, Any], cwd: Path) -> None:
    session = {"id": init.get("session_id"), "cwd": str(cwd), "model": init.get("model")}
    conn.execute("UPDATE conversations SET agent_session = ? WHERE id = ?", (json.dumps(session), conversation_id))
