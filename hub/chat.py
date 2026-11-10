"""Runs one completion against the gateway and streams it to the event bus.

A run is started by posting a user message. It works in the background and reports over the event bus:
    chat.message  (persisted)  a finished message (the user's, then the assistant's)
    chat.loading  (ephemeral)  provider still getting ready (model swap)
    chat.delta    (ephemeral)  a reasoning or content delta
    chat.done     (persisted)  the run finished: usage, stats, cost, stop reason, cancelled?
    chat.error    (persisted)  the run failed: detail. A refusal by the provider is an error too, but its
                               tokens were spent, so its usage and cost are recorded and sent along.
Deltas are not stored in the events table (too many); the finished answer lives in the messages table (D14),
together with the provider's message as received, which the next request replays (D7).
The app follows a run by subscribing to 'chat.*' and the conversation_id in each payload.

With projects attached to the chat (stage 3, D27) each user message is followed by a stored system message with
the context (hub/context.py, D22), and the model gets the hub's reading tools (hub/tools.py). A run may then take
several steps: an answer that asks for tools is stored as its own assistant message (with its usage and cost),
the hub runs the tools and stores their results as a message of role "tool", and the model is asked again - up to
MAX_TOOL_STEPS times (an answer cut there says so: `limited`). chat.done ends the run with its totals. Nothing
stored is ever rewritten, and the tool list of a conversation does not change, so Claude's thinking stays valid
(D14). A cloud model reaches only projects that allow the cloud and sees the others by name (D10); it gets no chat
where a local model worked with such a project.

The model may also write files of the chat's projects (stage 5, D17): write_file and edit_file are asked in the
approval queue with a diff, unless the chat is trusted or an answer "for this chat" / a rule of the project covers
them; a read-only chat refuses them. Each write is kept as a change that can be undone (hub/changes.py). In a chat
with no project a project tool makes the hub ask the user which of their projects (31.2, the user's choice); the one
picked is attached, as the chip does, and the model gets its context before the next step. And it may
run commands (run_command, hub/commands.py) in a hub terminal: asked unless the project's whitelist or an answer for
the chat covers that very command - destructive and network commands always. A trusted chat asks nothing but a
destructive action (the user's word 2026-10-28: commands, the network and sending too) - until it reads the web
(D19): from then on it asks like any other.

A chat with a Claude Code model (stage 4, D8) runs the user's `claude` CLI in the chat's project instead
(hub/claude_code.py): same events, same stored messages; Claude Code reads the ecosystem through the hub's MCP and
keeps its own session, so the hub stores no context messages for it. Other models get its answers as text only:
its tool calls belong to Claude Code's tools, not the hub's.

A chat with a goal (D44, hub/goals.py) works turn after turn: when a turn ends, the goal checks its criteria and
either stops or starts the next turn itself (go_on) with a note of the hub - what is missing, what the user added.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from common.i18n import tr
from common.logging_setup import log_task_exception

from . import (
    agent_tools,
    attachments,
    autonomous,
    budgets,
    cards,
    changes,
    commands,
    compact,
    computer,
    context,
    fit,
    guard,
    limits,
    links,
    mail,
    mcp_client,
    memory,
    model_settings,
    notes,
    sandbox,
    store,
    visual,
    web,
    websearch,
)
from .approvals import Decision, beyond_full, trusted_skips
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database
from .events import EventBus
from .gateway import Completion, Gateway, GatewayError, ProviderError, blocks_to_prompt_text
from .gateway.base import cost_usd, tool_result_block
from .projects import ProjectError, get_project, list_projects
from .secret_filter import clean
from .symbols import SymbolIndex
from .tools import FIRST_TOOLS, TOOLS, ToolContext, ToolError, run_tool

log = logging.getLogger("hub.chat")

DEFAULT_EFFORT = "medium"
TITLE_LENGTH = 60
MAX_TOOL_STEPS = 30  # 31.3: coder makes one call a step - 12 did not hold an app of a few files with its tests
NOT_RUN = "not run: the run stopped before this call"
READ_ONLY = "not done: this chat is read-only - the user did not allow changes. Say what you would change instead."
CHAT_TOOLS = [
    *TOOLS,
    *changes.WRITE_TOOLS,
    commands.RUN_COMMAND,
    web.WEB_FETCH,
    websearch.WEB_SEARCH,
    autonomous.ASK_USER,  # a question with answers to pick (2026-10-28)
]  # a new chat's; older keep theirs
# what only looks: an autonomous run may use it before its plan (the hub's reading tools, the web)
READING = frozenset(
    {
        *(t["name"] for t in TOOLS),
        web.WEB_FETCH["name"],
        websearch.WEB_SEARCH["name"],
        "folder_list",
        "folder_read",
        *computer.READING,
        *mail.READING,
    }
)
STEP_LIMIT = "not run: the step limit for one answer was reached; answer now from what you already have"
# the tools that work in a project: in a chat with none, the hub asks the user which (31.2, the user's choice)
PROJECT_TOOLS = frozenset(t["name"] for t in (*TOOLS, *changes.WRITE_TOOLS, commands.RUN_COMMAND)
                          if "project" in (t.get("parameters") or {}).get("properties", {}))  # fmt: skip
NO_PROJECT_CHOSEN = ("not done: the user chose no project for this chat{said}. Give the code or the result in your "
                     "answer instead, and do not use the project tools again in this answer.")  # fmt: skip
NO_PROJECTS = (
    "not done: there are no projects in AFO yet - the user adds a folder in «Проекты» "  # i18n: keep
    "→ «Добавить проект…». Give the code or the result in your answer instead."  # i18n: keep
)
NO_CLOUD_PROJECTS = ("not done: no project of the user allows cloud models (D10), so none can be attached to this "
                     "chat. Give the code or the result in your answer instead.")  # fmt: skip
# a run of the proving ground (D45): nobody watches it - what would be asked is refused, a question answered at once
PROVING_NO = ("not done: on the proving ground nobody approves this (it is destructive, goes to the network, or "
              "comes after reading the web) - do the task another way")  # fmt: skip
PROVING_ASKED = ("nobody answers on the proving ground: decide yourself from the task and the files, and go on; "
                 "say in your answer what you assumed")  # fmt: skip
# what brings in text someone else wrote - pages, searches, letters, an API's answers, other programs' screens -
# the hub's tools and Claude Code's: a chat that used one asks its writes and commands every time (D19)
OUTSIDE = (web.WEB_FETCH["name"], websearch.WEB_SEARCH["name"], *sorted(mail.READING), "http_request", "screen_look",
           "WebFetch", "WebSearch")  # fmt: skip
# a «yes» typed in answer to the hub's own question (33.2), not a button
YES = re.compile(r"\s*(да|yes|ok|ок|разрешаю|разрешить)\b", re.IGNORECASE)  # i18n: keep


class ChatService:
    def __init__(self, db: Database, bus: EventBus, gateway: Gateway):
        self._db = db
        self._bus = bus
        self._gateway = gateway
        self._runs: dict[str, asyncio.Event] = {}  # conversation_id -> cancel flag of its active run
        self.terminals = None  # set by the server: the tools read terminals and processes through them
        self.processes = None
        self.symbols = SymbolIndex()  # the server's shared one replaces it
        self.claude_code = None  # set by the server: runs the turns of chats with a Claude Code model
        self.approvals = None
        self.snapshots: Path | None = None  # data/snapshots: the files before and after each write
        self.agents = None  # set by the server: a coordinator's sub-runs (D20)
        self.secrets = None  # set by the server: the search engine's key (web_search)
        self.editor = None  # set by the server: what VS Code reports (stage 6.6)
        self.autonomy: autonomous.Autonomy | None = None  # set by the server: the autonomous agent's tools (9.1)
        self.toolbox: agent_tools.AgentToolbox | None = None  # its folders, scripts, requests, notices (9.4)
        self.mcp: mcp_client.McpServers | None = None  # outside MCP servers' tools (9.5, D42)
        self.computer: computer.Computer | None = None  # the autonomous agent's hands on the desktop (9.6, D41)
        self.mail: mail.MailTools | None = None  # the user's mailbox (9.4b, Q21)
        self.sandboxes: Path | None = None  # data/worktrees: «Песочница» chats write there (D17, 9.4b)
        self.goals = None  # set by the server: a chat's goal hears how each of its turns ended (D44, 11.1)
        self.blobs: Path | None = None  # data/blobs: the files and pictures sent with messages (2026-10-29)
        self.visual: visual.Visual | None = None  # set by the server: pictures a model asks for (29.6, D55)
        self.proving: set[str] = set()  # the proving ground's runs' chats: nobody answers there (D45)
        self.guards: dict[str, guard.TurnGuard] = {}  # a goal's turn: its constraints, an investigation (11.4)
        self.compact = None  # a goal's stages done as one line each in the request (11.5; hub/goals.py sets it)
        # chats whose Claude Code went to read the web (D19): known at once, before its results are stored
        self.outside: set[str] = set()
        # «Песочница» chats' git projects whose worktree could not be made: project id -> why (D46)
        self.sandbox_broken: dict[str, dict[str, str]] = {}
        # what each chat's model read: "<project id>:<path>" -> sha256 (D46; tools.ToolContext.seen)
        self.seen_files: dict[str, dict[str, str]] = {}
        # chats whose user answered «Без проекта» in the answer going on: not asked again in it (31.2)
        self._no_project: set[str] = set()
        # what waits for a chat's turn to end, in order: «Применить» in its sandbox asked while the model wrote (19.7)
        self.after_turn: dict[str, list[Callable[[], Awaitable[None]]]] = {}

    @property
    def busy(self) -> list[str]:
        return list(self._runs)

    async def send(
        self,
        conversation_id: str,
        text: str,
        model_id: str,
        *,
        effort: str = DEFAULT_EFFORT,
        client_id: str | None = None,
        confirm_over_budget: bool = False,
        files: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Appends the user message and starts the assistant run. Returns the stored user message.

        A paid model whose budget is used up is refused before anything is stored (BudgetReached), unless every
        such budget allows asking and the user confirmed (`confirm_over_budget`); the override is logged.
        `files`: files and pictures sent with the message ({name, data (base64)}, hub/attachments.py).
        """
        if conversation_id in self._runs:
            raise GatewayError("this conversation already has a run in progress")
        model = await self._db.acall(lambda conn: store.get_model(conn, model_id))
        if model is None:
            raise GatewayError(f"unknown model {model_id!r}")
        if not model["enabled"]:
            raise GatewayError(f"model {model_id!r} is not available now (its provider no longer lists it)")
        if model["locality"] == "cloud":
            await self._check_cloud(conversation_id)
        sent = await self._attached_files(model, files or [])  # kept once by their content: a resend takes the same
        user_blocks = [*([{"type": "text", "text": text}] if text.strip() or not sent else []), *sent]
        if model["prices"]:  # local models are free and never held back
            estimate = await self._upper_cost(conversation_id, model, user_blocks)
            await self._check_budgets(model_id, confirm_over_budget, client_id, estimate)

        def write(conn):
            title = None
            if not store.get_title(conn, conversation_id):  # an untitled chat is named after its first message
                title = _title_from(text if text.strip() else ", ".join(b["name"] for b in sent))
                store.set_title(conn, conversation_id, title)
            return store.add_message(conn, conversation_id, "user", user_blocks), title

        user_msg, title = await self._db.acall(write)
        if title:
            await self._bus.publish("chat.titled", {"conversation_id": conversation_id, "title": title})
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": user_msg.to_json()})
        agent = model["provider"] == CLAUDE_CODE
        if not agent:
            await self._use_limits(model_id)  # its context is stored within the model's own budgets (D39)
            await self._store_context(conversation_id)
        cancel = asyncio.Event()
        self._runs[conversation_id] = cancel
        if agent:
            if sent:  # Claude Code reads the files itself, from where they are put for it
                text = await asyncio.to_thread(self._files_for_agent, conversation_id, user_msg.id, text, sent)
            task = asyncio.create_task(self._run_agent(conversation_id, model, text, cancel, client_id, effort=effort))
        else:
            task = asyncio.create_task(self._run(conversation_id, model, effort, cancel, client_id))
        task.add_done_callback(log_task_exception)
        return user_msg.to_json()

    async def _attached_files(self, model: dict, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The blocks of the files sent with a message - refused (GatewayError) when one cannot be read, or when
        the model, as its settings say, does not see pictures."""
        if not files:
            return []
        if self.blobs is None:
            raise GatewayError("files are not kept in this hub")
        try:
            found = await asyncio.to_thread(attachments.blocks, self.blobs, files)
        except attachments.AttachmentError as exc:
            raise GatewayError(str(exc)) from None
        sees = (model.get("capabilities") or {}).get("vision")
        if attachments.has_images(found) and model["provider"] != CLAUDE_CODE and sees is False:
            raise GatewayError(tr("«{name}» не видит картинки: выберите модель со зрением — или отметьте «картинки» в "
                                  "настройке модели, если она их видит", name=model["name"]))  # fmt: skip
        return found

    def _files_for_agent(self, conversation_id: str, message_id: int, text: str, sent: list[dict[str, Any]]) -> str:
        """Claude Code's message with the files: put in a folder of the chat, their paths said (blocking)."""
        folder = self.blobs.parent / "attachments" / conversation_id / str(message_id)  # type: ignore[union-attr]
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for block in sent:
            data = attachments.load(self.blobs, block["blob"]) or b""  # type: ignore[arg-type]
            target = folder / (Path(block["name"]).name or "file")
            target.write_bytes(data)
            paths.append(str(target))
        listed = "\n".join(f"- {p}" for p in paths)
        return (f"{text}\n\n" if text.strip() else "") + f"Files sent with this message (read them):\n{listed}"

    async def _check_budgets(self, model_id: str, confirmed: bool, client_id: str | None, estimate: float) -> None:
        """A used-up budget, or one with less left than the request may cost (`estimate`, stage 20.2), holds a paid
        request back: BudgetReached - unless the user confirmed and every such budget allows asking."""
        over = await self._db.acall(lambda conn: budgets.too_dear(conn, model_id, estimate))
        if not over:
            return
        can_confirm = all(b["hard_action"] == "ask" for b in over)
        if not (confirmed and can_confirm):
            raise BudgetReached(over, can_confirm)
        await self._bus.publish("budget.overridden", {"model_id": model_id, "client_id": client_id, "budgets": over})

    async def _upper_cost(self, conversation_id: str, model: dict, adding: list[dict[str, Any]] | str) -> float:
        """The most this chat's next request to a paid `model` may cost (stage 20.2): the conversation so far with its
        system prompt and tool definitions, and what comes with the request (`adding`: a new message's blocks, a
        hub note) - no more than the model's window, which the request is fitted to (hub/fit.py) - and the longest
        answer. A rough measure, as fit's; each later step of the run is weighed again on its own request."""
        accepts = self._gateway.accepts(model["id"])

        def gather(conn):
            return store.get_messages(conn, conversation_id), _answer_setup(conn, conversation_id, model["id"], accepts)

        history, (system, params, _version) = await self._db.acall(gather)
        window = (model.get("capabilities") or {}).get("context")
        tools = compact.for_model([*CHAT_TOOLS, *(self.mcp.definitions() if self.mcp is not None else [])], window)
        prompt = [{"role": "system", "content": system},
                  *_to_prompt(history, cloud=model.get("locality") == "cloud", blobs=self.blobs)]  # fmt: skip
        images = sum(1 for b in adding if isinstance(b, dict) and b.get("type") == "image")
        tokens = fit.estimate(prompt, tools) + fit.tokens(adding) + fit.IMAGE_TOKENS * images
        return budgets.upper_cost(model, min(tokens, window) if window else tokens, params)

    def cancel(self, conversation_id: str) -> bool:
        cancel = self._runs.get(conversation_id)
        if cancel is None:
            return False
        cancel.set()
        return True

    async def start(
        self,
        conversation_id: str,
        model_id: str,
        text: str | None,
        *,
        limits: Limits,
        on_end: Callable[[Outcome], Awaitable[None]],
        client_id: str | None = None,
        effort: str = DEFAULT_EFFORT,
    ) -> None:
        """An agent's run (stage 5, D16): its task - or nothing new, when a run goes on after the hub restarted - and
        then the same loop as a chat's, within the run's limits; `on_end` hears how it ended."""
        if conversation_id in self._runs:
            raise GatewayError("this conversation already has a run in progress")
        model = await self._db.acall(lambda conn: store.get_model(conn, model_id))
        if model is None or not model["enabled"]:
            raise GatewayError(f"model {model_id!r} is not available now")
        if model["locality"] == "cloud":
            await self._check_cloud(conversation_id)
        if model["prices"]:
            estimate = await self._upper_cost(conversation_id, model, text or "")
            await self._check_budgets(model_id, False, client_id, estimate)
        if text:
            message = await self._db.acall(
                lambda conn: store.add_message(conn, conversation_id, "user", [{"type": "text", "text": text}])
            )
            await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})
        await self._use_limits(model_id)
        await self._store_context(conversation_id)
        cancel = asyncio.Event()
        self._runs[conversation_id] = cancel
        task = asyncio.create_task(
            self._run(
                conversation_id, model, effort, cancel, client_id, limits=limits, outcome=Outcome(), on_end=on_end
            )
        )
        task.add_done_callback(log_task_exception)

    async def go_on(
        self,
        conversation_id: str,
        model_id: str,
        note: str,
        *,
        limits: Limits,
        effort: str = DEFAULT_EFFORT,
        client_id: str | None = None,
        mark: dict[str, Any] | None = None,
    ) -> None:
        """A turn the hub starts itself in a chat - a goal's next one (D44): its word to the model (a hub note, not
        the user's; `mark` - who works the turn and why, when the goal has two models, 11.7), then the chat's loop -
        or Claude Code's turn, within `limits.max_steps` of its own (11.7); the goal hears how the turn ended, like
        any turn of that chat. Raises GatewayError when it cannot start (busy, the model gone, the cloud or a budget
        says no)."""
        if conversation_id in self._runs:
            raise GatewayError("this conversation already has a run in progress")
        cancel = self._runs[conversation_id] = asyncio.Event()  # taken at once: nothing starts in between
        try:
            model = await self._db.acall(lambda conn: store.get_model(conn, model_id))
            if model is None or not model["enabled"]:
                raise GatewayError(f"model {model_id!r} is not available now")
            agent = model["provider"] == CLAUDE_CODE
            mode = (await self._db.acall(lambda conn: store.context_settings(conn, conversation_id)))["mode"]
            if agent and mode == "sandbox":  # it would work in the project, the checks in the copy
                raise GatewayError("Claude Code works in the project itself, not in «Песочница»")  # i18n: keep
            if model["locality"] == "cloud":
                await self._check_cloud(conversation_id)
            if model["prices"]:
                estimate = await self._upper_cost(conversation_id, model, note)
                await self._check_budgets(model_id, False, client_id, estimate)
            await self._hub_note(conversation_id, note, mark)
            if not agent:
                await self._use_limits(model_id)
                await self._store_context(conversation_id)
        except BaseException:
            self._runs.pop(conversation_id, None)
            raise
        if agent:
            task = asyncio.create_task(self._run_agent(conversation_id, model, note, cancel, client_id,
                                                       max_turns=limits.max_steps, effort=effort))  # fmt: skip
        else:
            task = asyncio.create_task(self._run(conversation_id, model, effort, cancel, client_id, limits=limits))
        task.add_done_callback(log_task_exception)

    async def tool_projects(self, conversation_id: str) -> list:
        """The chat's projects as its tools see them: in «Песочница» a git project is its worktree (D17, 9.4b)."""
        attached, settings = await self._db.acall(
            lambda conn: (_attached(conn, conversation_id), store.context_settings(conn, conversation_id))
        )
        if settings["mode"] == "sandbox" and self.sandboxes is not None:
            broken = self.sandbox_broken.setdefault(conversation_id, {})
            return await asyncio.to_thread(_sandboxed, self.sandboxes, conversation_id, attached, broken)
        return attached

    async def _run(
        self,
        conversation_id: str,
        model: dict,
        effort: str,
        cancel: asyncio.Event,
        client_id: str | None,
        *,
        limits: Limits | None = None,
        outcome: Outcome | None = None,
        on_end: Callable[[Outcome], Awaitable[None]] | None = None,
    ):
        outcome = outcome or Outcome()
        began = time.monotonic()
        try:
            await self._steps(conversation_id, model, effort, cancel, client_id, limits or Limits(), outcome)
            if outcome.status == "running":
                outcome.status = "done"
        except GatewayError as exc:
            outcome.status, outcome.detail = "error", str(exc)
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": str(exc),
                                                   "model_id": model["id"]})  # fmt: skip
        except Exception:
            log.exception("chat run for %s crashed", conversation_id)
            outcome.status, outcome.detail = "error", "internal error"
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": "internal error",
                                                   "model_id": model["id"]})  # fmt: skip
        finally:
            outcome.seconds = time.monotonic() - began
            await self._after_turn(conversation_id)
            self._runs.pop(conversation_id, None)
            if on_end is not None:
                if self.approvals is not None:
                    await self.approvals.expire(conversation_id)
                if self.autonomy is not None:
                    await self.autonomy.expire_all(conversation_id)  # its questions too: nobody waits for them now
                await on_end(outcome)
            elif self.goals is not None and not _stopping():  # a chat's turn: its goal checks it, goes on or not
                # (D44) - not when the hub stops: the goal goes on after the restart
                try:
                    await self.goals.turn_ended(conversation_id, outcome)
                except Exception:
                    log.exception("the goal of %s did not take the turn's end", conversation_id)

    async def _after_turn(self, conversation_id: str) -> None:
        """What waited for the turn to end (19.7) - the chat still busy meanwhile, so what is asked during it waits
        too and runs here as well; before the goal hears of the turn, so its next turn sees it done."""
        while jobs := self.after_turn.pop(conversation_id, None):
            for job in jobs:
                try:
                    await job()
                except Exception:
                    log.exception("what waited for the turn of %s failed", conversation_id)

    async def _run_agent(self, conversation_id: str, model: dict, text: str, cancel: asyncio.Event, client_id, *,
                         max_turns: int | None = None, effort: str | None = None):  # fmt: skip
        """A turn of Claude Code in the chat's projects - those a cloud model may reach (D10) - or, with none
        attached, in a folder of the chat's own; a goal's turn within `max_turns` of its own; at the chat's depth
        (`effort`, 21.2). Its end goes to the chat's goal as any turn's (11.7)."""
        outcome = Outcome()
        began = time.monotonic()
        try:
            if self.claude_code is None:
                raise GatewayError("Claude Code is not set up in this hub")
            attached = await self._db.acall(lambda conn: _attached(conn, conversation_id))
            usable = [p for p in attached if p.cloud_ok]
            if attached and not usable:
                names = ", ".join(f"«{p.name}»" for p in attached)
                raise GatewayError(
                    f"{names}: only local models may see it (D10), and Claude Code is a cloud model. "
                    "Allow the cloud in the project's settings to work on it with Claude Code."
                )
            await self._bus.publish(
                "chat.run_started", {"conversation_id": conversation_id, "model_id": model["id"]}, persist=False
            )
            ended = await self.claude_code.run(conversation_id, model, text, usable, cancel, client_id,
                                               max_turns=max_turns, effort=effort)  # fmt: skip
            outcome.totals = RunTotals()
            outcome.totals.add(ended["usage"], 0.0)  # the subscription pays (D12)
            outcome.status = "cancelled" if ended["cancelled"] else "error" if ended["error"] else "done"
            outcome.detail, outcome.limited = ended["error"], ended["limited"]
            outcome.subscription = ended.get("rejected")
        except GatewayError as exc:
            outcome.status, outcome.detail = "error", str(exc)
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": str(exc),
                                                   "model_id": model["id"]})  # fmt: skip
        except Exception:
            log.exception("claude code turn for %s crashed", conversation_id)
            outcome.status, outcome.detail = "error", "internal error"
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": "internal error",
                                                   "model_id": model["id"]})  # fmt: skip
        finally:
            outcome.seconds = time.monotonic() - began
            await self._after_turn(conversation_id)
            self._runs.pop(conversation_id, None)
            if self.approvals is not None:
                await self.approvals.expire(conversation_id)  # nobody waits for its open requests now
            if self.autonomy is not None:
                await self.autonomy.expire_all(conversation_id)
            if self.goals is not None and not _stopping():  # its goal checks the turn, as any (11.7)
                try:
                    await self.goals.turn_ended(conversation_id, outcome)
                except Exception:
                    log.exception("the goal of %s did not take the turn's end", conversation_id)

    async def _steps(self, conversation_id, model: dict, effort: str, cancel, client_id, limits: Limits, outcome):
        self._no_project.discard(conversation_id)  # «Без проекта» holds for one answer (31.2)

        async def setup(model: dict):
            """What answering with this model takes: its provider, the projects it may see (read again: one may have
            been attached while the answer went on, 31.2), the system prompt and the answer's parameters the user
            set (D39)."""
            attached = await self._db.acall(lambda conn: _attached(conn, conversation_id))
            provider, model_ref = self._gateway.resolve(model["id"])
            await self._use_limits(model["id"])  # the tools' portions of this model (D22, D39)
            cloud = model.get("locality") == "cloud"
            visible = [p for p in attached if p.cloud_ok or not cloud]
            mode = (await self._db.acall(lambda conn: store.context_settings(conn, conversation_id)))["mode"]
            if mode == "sandbox" and self.sandboxes is not None:  # its tools see each git project's worktree
                broken = self.sandbox_broken.setdefault(conversation_id, {})
                visible = await asyncio.to_thread(_sandboxed, self.sandboxes, conversation_id, visible, broken)
            ctx = ToolContext(visible, terminals=self.terminals, processes=self.processes, symbols=self.symbols,
                              seen=self.seen_files.setdefault(conversation_id, {}))  # fmt: skip
            accepts = self._gateway.accepts(model["id"])
            model_id = model["id"]
            system, params, version = await self._db.acall(
                lambda conn: _answer_setup(conn, conversation_id, model_id, accepts)
            )
            return provider, model_ref, cloud, ctx, system, params, version

        provider, model_ref, cloud, ctx, system, params, version = await setup(model)
        await self._bus.publish(
            "chat.run_started", {"conversation_id": conversation_id, "model_id": model["id"]}, persist=False
        )
        run = outcome.totals = RunTotals()
        fallback: dict | None = None  # the spare took over (once per run): from which model, and why
        tools = None
        scale, squeezed = 1.0, 0  # how far the length guess falls short for this model; requests sent shorter again
        last_words = 2 if limits.autonomous and self.autonomy is not None else 0  # turns to mark a step and report
        for step in range(limits.max_steps + 2 + last_words):
            if limits.autonomous and self.autonomy is not None:  # paused by the user; what it should hear first
                await self.autonomy.wait_resumed(conversation_id, cancel)
                if cancel.is_set():
                    outcome.status = "cancelled"
                    return
                for notice in self.autonomy.take_notices(conversation_id):
                    await self._hub_note(conversation_id, notice)
            history = await self._db.acall(lambda conn: store.get_messages(conn, conversation_id))
            if self.compact is not None:  # a goal's stages done: one line each (11.5), the conversation stays whole
                history = await self.compact(conversation_id, history)
            prompt = _to_prompt(history, cloud=cloud, blobs=self.blobs)
            if system:  # in front, the same in every request: the fixed part of the prompt (cache, D15)
                prompt = [{"role": "system", "content": system}, *prompt]
            # the tool list never changes within a conversation (D14), and every model has it from the first message:
            # the web tools are for any chat, with projects or without (D19) - the definitions it was first sent,
            # even after the hub's tools changed
            if tools is None:  # a new chat gets the outside MCP servers' tools ready now too (D42) - sized to its
                # model's window (hub/compact.py; projects may come later in a chat: their tools stay)
                pictures = [visual.GENERATE_IMAGE] if self.visual is not None and await self.visual.can_draw() else []
                if self.visual is not None and await self.visual.can_film():  # a video model too (29.8б)
                    pictures.append(visual.GENERATE_VIDEO)
                current = compact.for_model([*CHAT_TOOLS, *pictures,
                                             *(self.mcp.definitions() if self.mcp is not None else [])],
                                            (model.get("capabilities") or {}).get("context"))  # fmt: skip
                tools = await self._db.acall(
                    lambda conn, current=current: store.conversation_tools(conn, conversation_id, current, FIRST_TOOLS)
                )
            # a long run outgrows the model's window: the oldest tool results give way in this request (hub/fit.py)
            prompt, shortened = fit.fit(prompt, window=(model.get("capabilities") or {}).get("context"), tools=tools,
                                        scale=scale)  # fmt: skip
            if shortened:
                log.info("conversation %s: %d old messages shortened to fit %s's window", conversation_id, shortened,
                         model["id"])  # fmt: skip
            if step and model["prices"]:  # the run's first request was weighed before it started (stage 20.2)
                estimate = budgets.upper_cost(model, int(fit.estimate(prompt, tools) * scale), params)
                over = await self._db.acall(lambda conn, m=model["id"], e=estimate: budgets.too_dear(conn, m, e))
                if over and all(b["state"] == "hard" for b in over):
                    raise GatewayError("the budget ran out while the model was working; send again to go on")
                if over:
                    raise GatewayError(f"{budgets.held_back(over, 'the next request')}; send again to go on")
            try:
                completion = await self._consume(conversation_id, provider, model_ref, prompt, effort, cancel, tools,
                                                 params)  # fmt: skip
            except ProviderError as exc:  # the model failed: the spare answers instead, once (D39)
                counted = fit.overflow(exc.detail)
                if counted is not None and squeezed < 2 and not cancel.is_set():  # too long after all: shorter again
                    squeezed += 1
                    found = fit.calibrate(counted, prompt, tools) if counted else scale * 1.3
                    scale = max(scale * 1.15, found * 1.05)
                    log.info("conversation %s: %s refused the length (%s tokens): shorter again, x%.2f",
                             conversation_id, model["id"], counted or "?", scale)  # fmt: skip
                    continue
                spare = None if fallback or cancel.is_set() else await self._spare(conversation_id, model)
                if spare is None:
                    raise
                fallback = await self._fall_back(conversation_id, model, spare, exc.detail)
                model = spare
                provider, model_ref, cloud, ctx, system, params, version = await setup(model)
                continue
            completion.settings_version = version
            completion.fallback = fallback
            used = completion.usage or {}
            counted = sum(int(used.get(k) or 0) for k in ("input_tokens", "cache_read", "cache_write"))
            if counted and not used.get("estimated"):  # the model's own count corrects the guess for the next step
                scale = fit.calibrate(counted, prompt, tools)
            if completion.refusal:
                spare = None if fallback else await self._spare(conversation_id, model)
                cost = await self._save_refusal(conversation_id, model, completion, client_id, final=spare is None)
                if spare is not None:  # it declined: the spare may answer (its tokens are counted all the same)
                    fallback = await self._fall_back(conversation_id, model, spare, completion.refusal,
                                                     spent=(completion.usage, cost))  # fmt: skip
                    model = spare
                    provider, model_ref, cloud, ctx, system, params, version = await setup(model)
                    continue
                outcome.status, outcome.detail = "error", completion.refusal
                return
            stopped_short = completion.stop_reason in ("max_tokens", "length")  # a cut-off call must not run
            if not completion.tool_calls or completion.cancelled or stopped_short:
                completion.tool_calls = []
                nudge = None
                if limits.autonomous and self.autonomy is not None and not completion.cancelled:
                    nudge = await self.autonomy.nudge(conversation_id)  # it stopped before its report (D40)
                if nudge:
                    await self._save_step(conversation_id, model, completion, client_id, run)
                    await self._hub_note(conversation_id, nudge)
                    continue
                await self._save(conversation_id, model, completion, client_id, run)
                if completion.cancelled:
                    outcome.status = "cancelled"
                return
            message_id = await self._save_step(conversation_id, model, completion, client_id, run)
            if step >= limits.max_steps:  # it keeps asking: answer without running, and let it finish (or stop)
                last = bool(last_words)  # an autonomous run may still mark its step and report
                await self._answer_tools(conversation_id, completion.tool_calls, ctx, cancel,
                                         refuse=autonomous.LIMIT if last else STEP_LIMIT, counted=last,
                                         let=autonomous.LAST_WORDS if last else frozenset())  # fmt: skip
                if step > limits.max_steps + last_words:
                    outcome.status, outcome.detail = "error", f"the step limit ({limits.max_steps}) was reached"
                    outcome.limited = True  # the window says so under the answer (31.3)
                    await self._bus.publish("chat.done", {**run.done(conversation_id, message_id, completion),
                                                          "limited": True})  # fmt: skip
                    return
                continue
            await self._answer_tools(conversation_id, completion.tool_calls, ctx, cancel, cloud=cloud,
                                     counted=limits.autonomous)  # fmt: skip
            switch = self.autonomy.take_switch(conversation_id) if self.autonomy is not None else None
            if switch:  # the planner made the plan: the run's own model carries it out
                found = await self._db.acall(lambda conn, m=switch: store.get_model(conn, m))
                if found is not None and found["enabled"]:
                    if found["locality"] == "cloud":  # the planner may have read a local-only project for it (D10)
                        await self._check_cloud(conversation_id)
                    model = found
                    provider, model_ref, cloud, ctx, system, params, version = await setup(model)
                    await self._bus.publish("chat.fallback", {"conversation_id": conversation_id,
                                                              "model_id": model["id"], "name": model["name"],
                                                              "from": "", "from_name": "", "reason": "",
                                                              "planned": True})  # fmt: skip
            if cancel.is_set():
                await self._bus.publish("chat.done", run.done(conversation_id, message_id, completion, cancelled=True))
                outcome.status = "cancelled"
                return
            if limits.deadline is not None and time.monotonic() > limits.deadline:
                raise GatewayError("the task's time limit was reached; give it more time or a narrower task")
            if limits.budget_usd is not None and run.cost >= limits.budget_usd:
                raise GatewayError(f"the task's budget (${limits.budget_usd:g}) was reached at ${run.cost:.4f}")

    async def _hub_note(self, conversation_id: str, text: str, mark: dict[str, Any] | None = None) -> None:
        """A word from the hub to the model between its steps (D40): stored as a user turn, marked as the hub's
        (`mark`: which model works the goal's turn, and why - 11.7; the window shows it, the model does not)."""
        block = {"type": "text", "text": text, "hub": True, **({"worker": mark} if mark else {})}
        message = await self._db.acall(lambda conn: store.add_message(conn, conversation_id, "user", [block]))
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})

    async def _use_limits(self, model_id: str) -> None:
        """The model's own context budgets and tool portions for the rest of this task (hub/limits.py)."""
        own = await self._db.acall(lambda conn: model_settings.current(conn, model_id)[1].get("context_budget"))
        limits.use(own)

    async def _spare(self, conversation_id: str, model: dict) -> dict | None:
        """The spare model (the role «fallback», D39) when it may answer here: another, offered model, not Claude
        Code (an agent of its own); a cloud one only where the chat's projects allow the cloud (D10); a paid one
        only within its budgets."""

        def find(conn):
            roles = model_settings.current(conn, model_settings.ALL)[1].get("roles") or {}
            spare_id = roles.get("fallback")
            if not spare_id or spare_id == model["id"]:
                return None
            spare = store.get_model(conn, spare_id)
            if spare is None or not spare["enabled"] or spare["provider"] == CLAUDE_CODE:
                return None
            if spare["prices"] and budgets.reached(conn, spare_id):
                return None
            return spare

        spare = await self._db.acall(find)
        if spare is not None and spare["locality"] == "cloud":
            try:
                await self._check_cloud(conversation_id)
            except GatewayError:
                return None
        return spare

    async def _fall_back(
        self, conversation_id: str, model: dict, spare: dict, reason: str, *, spent: tuple[dict, float] | None = None
    ) -> dict:
        """Tells the window that the spare answers now - and what the declined request still cost (`spent`); the
        answer keeps it too (its usage's `fallback`)."""
        log.info("chat %s: %s failed (%s), the spare %s answers", conversation_id, model["id"], str(reason)[:200],
                 spare["id"])  # fmt: skip
        note = {"from": model["id"], "from_name": model["name"], "reason": str(reason)[:300]}
        extra = {"usage": spent[0], "cost_usd": spent[1]} if spent else {}
        await self._bus.publish("chat.fallback", {"conversation_id": conversation_id, "model_id": spare["id"],
                                                  "name": spare["name"], **note, **extra})  # fmt: skip
        return note

    async def _store_context(self, conversation_id: str) -> None:
        """Right after a user message in a chat with projects: the context the model gets with it (D22), stored as
        a system message - the layers that changed since the last one, and the live state."""
        attached, instructions = await self._db.acall(
            lambda conn: (_attached(conn, conversation_id), _instructions(conn, conversation_id))
        )
        if not attached and not instructions:
            return

        def gather(conn):
            history = store.get_messages(conn, conversation_id)
            last = next((m.created_at for m in reversed(history) if m.role == "system"), None)
            since = last or (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            ids = [p.id for p in attached]
            rows = conn.execute(
                f"SELECT ts, type, project_id, payload FROM events WHERE ts > ? AND project_id IN "
                f"({', '.join('?' * len(ids))}) ORDER BY id",
                [since, *ids],
            ).fetchall()
            events = [{"ts": r["ts"], "type": r["type"], "project_id": r["project_id"],
                       "payload": json.loads(r["payload"])} for r in rows]  # fmt: skip
            all_links = links.list_links(conn)
            confirmed = {pid: cards.confirmed_text(conn, pid) for pid in [*ids, *_linked_ids(set(ids), all_links)]}
            written = {p.id: notes.list_notes(conn, p.id) for p in attached}
            return (history, list_projects(conn), events, store.context_settings(conn, conversation_id), confirmed,
                    written, all_links)  # fmt: skip

        history, everything, events, settings, confirmed, written, all_links = await self._db.acall(gather)
        ids = {p.id for p in attached}
        by_id = {p.id: p for p in everything}
        linked_ids = _linked_ids(ids, all_links)
        project_notes = {p.id: clean(notes.notes_layer(p.name, written[p.id])) for p in attached if written[p.id]}
        project_cards = {
            p.id: (confirmed[p.id], await asyncio.to_thread(cards.missing_paths, p, confirmed[p.id]))
            for p in attached
            if confirmed[p.id]
        }
        processes = (self.processes.latest or {}) if self.processes is not None else {}
        live = {}
        for p in attached:
            terminals = await self.terminals.list(p.id, 10) if self.terminals is not None else []
            news = [e for e in events if e["project_id"] == p.id]
            live[p.id] = await asyncio.to_thread(
                context.live_layer, p, processes=processes.get(p.id, []), terminals=terminals, events=news,
                now=datetime.now(UTC), editor_file=self.editor.open_in(p.id) if self.editor is not None else "",
            )  # fmt: skip
        linked, linked_cards = {}, {}
        for pid in linked_ids:
            other = by_id.get(pid)
            if other is None or not other.root.is_dir():
                continue
            relation = "; ".join(
                f"{by_id[link['a_id']].name} -> {by_id[link['b_id']].name}: {link['kind']}"
                + (f" ({link['description']})" if link["description"] else "")
                for link in all_links
                if link["status"] == "confirmed" and pid in (link["a_id"], link["b_id"])
                and (link["a_id"] in ids or link["b_id"] in ids)
            )  # fmt: skip
            linked_cards[pid] = (other, await asyncio.to_thread(context.linked_layer, other, confirmed[pid], relation))
            terminals = await self.terminals.list(pid, 10) if self.terminals is not None else []
            text = await asyncio.to_thread(
                context.live_layer, other, processes=processes.get(pid, []), terminals=terminals, events=[],
                now=datetime.now(UTC),
            )  # fmt: skip
            linked[pid] = (other, text.replace("<live_state", "<linked_project_state", 1).replace(
                "</live_state>", "</linked_project_state>"))  # fmt: skip
        known = {p.id: p for p in attached}
        pinned = {
            f"file:{item['project_id']}:{item['path']}": (known[item["project_id"]], item["path"])
            for item in settings["pinned"]
            if item["project_id"] in known  # a pinned file of a project no longer attached waits
        }
        files = {key: (p, await asyncio.to_thread(context.pinned_file, p, path)) for key, (p, path) in pinned.items()}
        blocks = await asyncio.to_thread(
            lambda: context.context_blocks(
                all_projects=everything, attached=attached, previous=context.previous_digests(history), live=live,
                settings=settings, pinned=files, cards=project_cards, notes=project_notes, links=all_links,
                linked=linked, linked_cards=linked_cards,
            )
        )  # fmt: skip
        if instructions:  # an agent's role (D16): once, right after the header
            role = context.role_block(instructions)
            if context.previous_digests(history).get(role["key"]) != role["digest"]:
                blocks.insert(1, role)
        message = await self._db.acall(lambda conn: store.add_message(conn, conversation_id, "system", blocks))
        await self._bus.publish(
            "chat.context",
            {"conversation_id": conversation_id, "message_id": message.id,
             "tokens": context.tokens(context.render(blocks, cloud=False))},
            persist=False,
        )  # fmt: skip

    async def _check_cloud(self, conversation_id: str) -> None:
        """A cloud model may not get a chat where a local model already worked with a local-only project (D10):
        its answers and tool results may hold that project's code."""

        def check(conn):
            local_only = {p.id: p.name for p in list_projects(conn) if not p.cloud_ok}
            by_name = {name.casefold(): name for name in local_only.values()} | {
                pid: n for pid, n in local_only.items()
            }
            seen: set[str] = set()  # local-only projects whose context is in the chat
            found: set[str] = set()
            calls: dict[str, str] = {}  # tool call id -> the local-only project it asked about
            for message in store.get_messages(conn, conversation_id):
                if message.role == "system":
                    for block in message.content:
                        key = block.get("key", "")
                        if ":" in key and not block.get("cloud_ok", True) and key.split(":", 1)[1] in local_only:
                            seen.add(local_only[key.split(":", 1)[1]])
                elif message.role == "assistant":
                    if seen and message.model_id:  # a local model answered with that context in front of it
                        model = store.get_model(conn, message.model_id)
                        if model is None or model["locality"] != "cloud":
                            found |= seen
                    for block in message.content:
                        args = block.get("input") if block.get("type") == "tool_call" else None
                        project = args.get("project") if isinstance(args, dict) else None
                        if isinstance(project, str) and (
                            name := by_name.get(project.strip().casefold(), by_name.get(project))
                        ):
                            calls[block["id"]] = name
                elif message.role == "tool":  # a tool already read that project for some model
                    found |= {calls[b["id"]] for b in message.content if b.get("id") in calls and not b.get("error")}
            return sorted(found)

        found = await self._db.acall(check)
        if found:
            names = ", ".join(f"«{n}»" for n in found)
            raise GatewayError(
                f"this chat already holds data of {names}, which only local models may see (D10): a cloud model "
                "may not get it. Start a new chat, or allow the cloud in the project's settings."
            )

    async def _answer_tools(
        self,
        conversation_id: str,
        calls: list[dict],
        ctx: ToolContext,
        cancel,
        *,
        refuse: str = "",
        cloud: bool = False,
        let: frozenset[str] = frozenset(),
        counted: bool = False,
    ) -> None:
        """Runs the calls (or answers them with `refuse` - but those in `let` -, or as not run once the user
        stopped) and stores the results as one message. `counted`: an autonomous run's - each call is counted
        (its plan standing still, D40)."""
        results = []
        had_projects = bool(ctx.projects)
        for call in calls:
            picture: dict[str, Any] = {}  # a picture the call drew (29.6): kept with the chat, shown under the call
            if (refuse and call["name"] not in let) or cancel.is_set():
                results.append(tool_result_block(call["id"], call["name"], refuse or NOT_RUN, True))
                continue
            if call["name"] in PROJECT_TOOLS and not ctx.projects:  # a chat with no project: which one (31.2)
                said = await self._pick_project(conversation_id, call, ctx, cloud, cancel)
                if said is not None:
                    results.append(tool_result_block(call["id"], call["name"], said, True))
                    continue
                if ctx.projects:  # the one picked, whatever name the model guessed
                    args = call["input"] if isinstance(call["input"], dict) else {}
                    call = {**call, "input": {**args, "project": ctx.projects[0].name}}
            await self._bus.publish(
                "chat.tool",
                {"conversation_id": conversation_id, "name": call["name"], "input": call["input"]},
                persist=False,
            )
            own = call["name"] in autonomous.TOOL_NAMES or call["name"] in memory.TOOL_NAMES
            outside = self.mcp is not None and mcp_client.split_name(call["name"]) is not None
            reading = call["name"] in READING or (outside and self.mcp.tool_class(call["name"]) == "read")
            acting = not reading and not own
            if acting and self.autonomy is not None and await self.autonomy.needs_plan(conversation_id):
                output, error = autonomous.PLAN_FIRST, True  # an autonomous run acts only after its plan (D40)
            elif call["name"] == autonomous.ASK_USER["name"] and conversation_id in self.proving:
                output, error = PROVING_ASKED, False  # nobody answers on the proving ground (D45)
            elif (refused := self._guarded(conversation_id, call, acting)) is not None:
                output, error = refused, True  # a goal's turn: its constraints, an investigation (11.4)
            elif call["name"] in changes.WRITE_TOOL_NAMES:
                output, error = await self._write_file(conversation_id, call, ctx, cancel)
            elif call["name"] == commands.RUN_COMMAND["name"]:
                output, error = await self._run_command(conversation_id, call, ctx, cancel)
            elif call["name"] == web.WEB_FETCH["name"]:
                output, error = await self._web_fetch(conversation_id, call, ctx, cancel)
            elif call["name"] == websearch.WEB_SEARCH["name"]:
                output, error = await self._web_search(conversation_id, call, ctx, cancel)
            elif call["name"] in (visual.GENERATE_IMAGE["name"], visual.GENERATE_VIDEO["name"]):  # here (29.6, 29.8б)
                output, error, picture = await self._draw(conversation_id, call, cancel)
            elif call["name"] in agent_tools.TOOL_NAMES and self.toolbox is not None:  # beyond projects (9.4)

                async def ask(tool, data, cover, call=call):
                    allowed, said = await self._ask_user(conversation_id, tool, data, None, cancel, cover=cover)
                    return None if allowed else said

                output, error = await self.toolbox.handle(conversation_id, call, cancel, ask, cloud=cloud)
            elif call["name"] in computer.TOOL_NAMES and self.computer is not None:  # the desktop (9.6, D41)

                async def ask(tool, shown, cover, call=call):
                    if tool == computer.ALLOW_PROGRAM:  # a program not allowed yet: the hub asks (33.2, D56)
                        return await self._allow_program(conversation_id, shown["program"], cancel)
                    if tool == "screen_act" and await self._on_its_own(conversation_id):
                        return None  # «Авто», «Полный»: in a program without a card; sending is still asked in «Авто»
                    allowed, said = await self._ask_user(conversation_id, tool, shown, None, cancel, cover=cover)
                    return None if allowed else said

                output, error = await self.computer.handle(conversation_id, call, cancel, ask, cloud=cloud)
            elif call["name"] in mail.TOOL_NAMES and self.mail is not None:  # the mailbox (9.4b, Q21)

                async def ask(tool, shown, cover, call=call):
                    allowed, said = await self._ask_user(conversation_id, tool, shown, None, cancel, cover=cover)
                    return None if allowed else said

                output, error = await self.mail.handle(conversation_id, call, cancel, ask, cloud=cloud)
            elif outside:  # an outside MCP server's (9.5, D42)
                output, error = await self._mcp_tool(conversation_id, call, ctx, cancel)
            elif own and self.autonomy is not None:  # an autonomous run's plan, steps and memory (D40)
                output, error = await self.autonomy.handle(conversation_id, call, cancel, cloud=cloud)
            elif call["name"] in ("delegate_task", "delegate_tasks"):  # a coordinator's (D20; a batch - D50)
                if self.agents is None:
                    output, error = "agents are not available here", True
                else:
                    args = call["input"] if isinstance(call["input"], dict) else {}
                    give = self.agents.delegate if call["name"] == "delegate_task" else self.agents.delegate_many
                    output, error = await give(conversation_id, args, cancel)
            else:
                output, error = await run_tool(call["name"], call["input"], ctx)
            results.append(tool_result_block(call["id"], call["name"], output, error) | picture)
            if counted and self.autonomy is not None:
                await self.autonomy.acted(conversation_id, call["name"])
        message = await self._db.acall(lambda conn: store.add_message(conn, conversation_id, "tool", results))
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})
        if ctx.projects and not had_projects:  # the user picked a project: its context, before the next step (D22)
            await self._store_context(conversation_id)

    async def _pick_project(
        self, conversation_id: str, call: dict, ctx: ToolContext, cloud: bool, cancel
    ) -> str | None:
        """A project tool in a chat with no project (31.2; the user's choice «Спросить проект», 2026-11-08): the hub
        asks in the chat which of the user's projects - a cloud model is offered only those that allow the cloud
        (D10), the one the model named first - and attaches the one picked, as the chip does; the call then runs in
        it. None: attached (or not asked: an agent's task, whose projects its form gave, D16; nobody to answer) - the
        call goes on as before; else what the model is told."""
        task = "SELECT 1 FROM agent_runs WHERE conversation_id = ?"
        if await self._db.acall(lambda conn: conn.execute(task, (conversation_id,)).fetchone()) is not None:
            return None
        if conversation_id in self._no_project:
            return NO_PROJECT_CHOSEN.format(said="")
        everything = await self._db.acall(list_projects)
        offered = [p for p in everything if p.cloud_ok or not cloud]
        if not offered:
            return NO_CLOUD_PROJECTS if everything else NO_PROJECTS
        if self.autonomy is None or conversation_id in self.proving:
            return None
        args = call["input"] if isinstance(call["input"], dict) else {}
        named = str(args.get("project") or "").strip().casefold()
        offered.sort(key=lambda p: p.name.casefold() != named)  # stable: the rest stay in the list's order
        none = tr("Без проекта")
        doing = (tr("создать или изменить файлы") if call["name"] in changes.WRITE_TOOL_NAMES
                 else tr("выполнить команду") if call["name"] == commands.RUN_COMMAND["name"]
                 else tr("посмотреть файлы проекта"))  # fmt: skip
        question = tr("Модель хочет {doing}, а у этого чата нет проекта. В каком проекте?", doing=doing)
        options = [p.name for p in offered[: autonomous.MAX_OPTIONS - 1]] + [none]
        answer = await self.autonomy.ask_question(conversation_id, question, options, cancel)
        if answer is None:
            return NOT_RUN
        chosen = _named_project(offered, answer)
        if chosen is None:
            self._no_project.add(conversation_id)
            said = "" if answer.strip() == none else f" (they answered: {answer.strip()[:300]})"
            return NO_PROJECT_CHOSEN.format(said=said)

        def attach(conn):
            ids = [*store.conversation_projects(conn, conversation_id), chosen.id]
            return store.set_conversation_projects(conn, conversation_id, ids), store.context_settings(
                conn, conversation_id
            )["mode"]

        attached, mode = await self._db.acall(attach)
        await self._bus.publish("chat.projects", {"conversation_id": conversation_id, "projects": attached},
                                persist=False)  # fmt: skip
        visible = [chosen]
        if mode == "sandbox" and self.sandboxes is not None:  # its tools see the project's worktree (9.4b)
            broken = self.sandbox_broken.setdefault(conversation_id, {})
            visible = await asyncio.to_thread(_sandboxed, self.sandboxes, conversation_id, visible, broken)
        ctx.add(*visible)
        return None

    def claude_guarded(self, conversation_id: str, tool_name: str, data: dict, root: Path | None) -> str | None:
        """Claude Code's edit or command in a goal's turn (11.7), asked through its permission tool: the guard of
        the hub's own tools - an investigation refuses it, and so does a constraint the hub checks."""
        turn = self.guards.get(conversation_id)
        found = guard.claude_call(tool_name, data if isinstance(data, dict) else {}, root) if turn else None
        if turn is None or found is None:
            return None
        if turn.investigating:
            return guard.INVESTIGATING
        said = guard.refusal(turn.rules, *found)
        if said:
            turn.refused.append(said)
        return said

    def _guarded(self, conversation_id: str, call: dict, acting: bool) -> str | None:
        """A goal's turn (11.4, hub/guard.py): an investigation refuses writing and commands (reading the web is
        reading); a constraint the hub checks refuses a write or a command - before anything is asked."""
        turn = self.guards.get(conversation_id)
        if turn is None:
            return None
        name = call["name"]
        if turn.investigating and acting and name not in (web.WEB_FETCH["name"], websearch.WEB_SEARCH["name"]):
            return guard.INVESTIGATING
        args = call["input"] if isinstance(call["input"], dict) else {}
        if name in changes.WRITE_TOOL_NAMES:
            said = guard.refusal(turn.rules, "write", str(args.get("path") or ""))
        elif name == commands.RUN_COMMAND["name"]:
            said = guard.refusal(turn.rules, "command", str(args.get("command") or ""))
        else:
            return None
        if said:
            turn.refused.append(said)
        return said

    async def _write_file(self, conversation_id: str, call: dict, ctx: ToolContext, cancel) -> tuple[str, bool]:
        """A write the model proposes: checked, asked (unless the chat is trusted or an answer covers it), done if
        the file is still as the user saw it, and recorded so it can be undone."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        settings = await self._db.acall(lambda conn: store.context_settings(conn, conversation_id))
        if settings["mode"] == "read_only":
            return READ_ONLY, True
        try:
            project = ctx.project(args.get("project"))
            planned = await asyncio.to_thread(changes.plan, call["name"], project, args)
        except (ToolError, changes.WriteError) as exc:
            return str(exc), True
        seen = ctx.seen if ctx.seen is not None else {}
        key = f"{project.id}:{planned.path}"
        if call["name"] == "write_file" and planned.before is not None and seen.get(key) != changes.digest(
                planned.before):  # fmt: skip
            return (f"not done: {planned.path} has changed since you read it, or you have not read it in this chat - "
                    "read it again first; a whole new content would lose what is there now"), True  # fmt: skip
        if settings["mode"] == "sandbox":  # into the worktree, unasked: the user reviews the diff (D17)
            done = await self._sandbox_write(conversation_id, project, planned)
            if not done[1]:
                seen[key] = changes.digest(planned.after)  # it knows its own write
            return done
        approval_id, chosen, comment = None, None, ""
        tainted = await self.read_the_web(conversation_id)
        unsaved = self.approvals is not None and self.approvals.unsaved.holds(call["name"], args, project.root)
        if settings["mode"] != "trusted" or tainted or unsaved:  # trust does not count after reading the web (D19),
            # nor over the user's edits not saved yet in the window's editor (D47)
            decision = await self._decision(conversation_id, call["name"], args, project, cancel, cover=not tainted)
            if isinstance(decision, str):
                return decision, True
            approval_id, chosen, comment = decision.approval_id, decision.hunks, decision.comment
        whole = planned
        if chosen is not None:  # allowed in part (17.2): the file rebuilt with the hunks chosen
            planned = await asyncio.to_thread(changes.compose, planned, set(chosen))
        if self.snapshots is None:
            return "not done: the hub keeps no snapshots here, so the change could not be undone", True
        try:
            hashes = await asyncio.to_thread(changes.apply, planned, self.snapshots)
        except changes.WriteError as exc:
            return str(exc), True
        except OSError as exc:
            return f"could not write {planned.path}: {exc.strerror or exc}", True
        if chosen is None:
            seen[key] = hashes[1]  # it knows its own write - not one done in part: it reads the file first
        change = await self._db.acall(lambda conn: changes.record(conn, project.id, planned, hashes, call["name"],
                                                                   conversation_id, approval_id))  # fmt: skip
        await self._bus.publish("file.changed", {"change": change, "project_id": project.id}, project_id=project.id)
        if chosen is not None:
            return await asyncio.to_thread(changes.partly, whole, set(chosen), change["id"], comment), False
        return f"{changes.summary(planned)} (change {change['id']}; the user can undo it)", False

    async def write_part(self, conversation_id: str, project, tool: str, data: dict, decision: Decision) -> str:
        """Claude Code's Edit or Write allowed in part (17.3, D51): its own tool would put all of it, so the hub
        writes the part - worked out as the card showed it, kept as a change - and says so for Claude Code to read."""
        hub = changes.as_hub_write(tool, data, project.root)
        if hub is None or self.snapshots is None or decision.hunks is None:
            return "not done: the hub could not write a part of this"
        chosen = set(decision.hunks)
        try:
            whole = await asyncio.to_thread(changes.plan_at, project.root, *hub)
            planned = await asyncio.to_thread(changes.compose, whole, chosen)
            hashes = await asyncio.to_thread(changes.apply, planned, self.snapshots)
        except changes.WriteError as exc:
            return f"not done: {exc}"
        except OSError as exc:
            return f"not done: could not write the file: {exc.strerror or exc}"
        change = await self._db.acall(lambda conn: changes.record(conn, project.id, planned, hashes, tool,
                                                                   conversation_id, decision.approval_id))  # fmt: skip
        await self._bus.publish("file.changed", {"change": change, "project_id": project.id}, project_id=project.id)
        said = await asyncio.to_thread(changes.partly, whole, chosen, change["id"], decision.comment)
        return f"Not run as asked: the user allowed a part of it, and the hub wrote that part. {said}"

    async def _sandbox_write(self, conversation_id: str, project, planned) -> tuple[str, bool]:
        if why := self.sandbox_broken.get(conversation_id, {}).get(project.id):
            return _no_sandbox(project, why), True
        if self.sandboxes is None or not project.root.is_relative_to(self.sandboxes):
            return (f"not done: «{project.name}» is not a git repository, and the sandbox needs one - the user may "
                    "switch the chat to «Спрашивать»"), True  # fmt: skip  # i18n: keep
        if self.snapshots is None:
            return "not done: the hub keeps no snapshots here", True
        try:
            await asyncio.to_thread(changes.apply, planned, self.snapshots)
        except changes.WriteError as exc:
            return str(exc), True
        except OSError as exc:
            return f"could not write {planned.path}: {exc.strerror or exc}", True
        await self._bus.publish("sandbox.changed", {"conversation_id": conversation_id, "project_id": project.id,
                                                    "path": str(planned.path)}, project_id=project.id)  # fmt: skip
        return (f"{changes.summary(planned)} - in the sandbox: the user sees the diff and applies it to the project "
                "or throws it away"), False  # fmt: skip

    async def _run_command(self, conversation_id: str, call: dict, ctx: ToolContext, cancel) -> tuple[str, bool]:
        """A command the model wants run: asked (the project's whitelist, the chat's answers and a trusted chat's
        ordinary commands aside), then run in a hub terminal the user sees, waited for."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        settings = await self._db.acall(lambda conn: store.context_settings(conn, conversation_id))
        if settings["mode"] == "read_only":
            return READ_ONLY, True
        try:
            project = ctx.project(args.get("project"))
            commands.command_of(args)
        except ToolError as exc:
            return str(exc), True
        if settings["mode"] == "sandbox" and (why := self.sandbox_broken.get(conversation_id, {}).get(project.id)):
            return _no_sandbox(project, why), True  # not in the project itself: the user chose a copy (D46)
        tainted = await self.read_the_web(conversation_id)
        if settings["mode"] != "trusted" or tainted or not trusted_skips(call["name"], args, project.root):
            allowed, why = await self._ask_user(conversation_id, call["name"], args, project, cancel,
                                                cover=not tainted)  # fmt: skip
            if not allowed:
                return why, True
        try:
            return await commands.run(self.terminals, project, args, by="model"), False
        except ToolError as exc:
            return str(exc), True
        except Exception as exc:  # a terminal that would not start: the model hears why, the run goes on
            log.exception("run_command failed")
            return f"the command did not start: {exc}", True

    async def _web_fetch(self, conversation_id: str, call: dict, ctx: ToolContext, cancel) -> tuple[str, bool]:
        """A page the model wants to read: asked unless the site is on the short list or the project's (D19), or
        the chat is trusted and holds no local-only project (D46)."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        try:
            url = web.check_url(args.get("url"))
        except ToolError as exc:
            return str(exc), True
        if web.site(url) not in web.ALLOWED_SITES and not await self.web_trusted(conversation_id):
            project = ctx.projects[0] if ctx.projects else None  # the chat's project keeps "always" answers
            allowed, why = await self._ask_user(conversation_id, call["name"], args, project, cancel)
            if not allowed:
                return why, True
        try:
            return await web.fetch(args), False
        except ToolError as exc:
            return str(exc), True

    async def _draw(self, conversation_id: str, call: dict, cancel) -> tuple[str, bool, dict[str, Any]]:
        """generate_image (29.6): drawn on this computer - nothing to ask; the PNG kept with the chat (data/blobs:
        named by a message, kept as long as it, D30) - the model hears only words of it."""
        if self.visual is None:
            return "pictures are not available here", True, {}
        video = call["name"] == visual.GENERATE_VIDEO["name"]  # a video's WebP (29.8б)
        output, error, data = await (self.visual.handle_video if video else self.visual.handle)(
            conversation_id, call, cancel)  # fmt: skip
        if data is None or self.blobs is None:
            return output, error, {}
        blob = await asyncio.to_thread(attachments.keep, self.blobs, data)
        return output, error, {"blob": blob, "media_type": "image/webp" if video else "image/png", "size": len(data)}

    async def _web_search(self, conversation_id: str, call: dict, ctx: ToolContext, cancel) -> tuple[str, bool]:
        """A search the model wants: asked every time - the query leaves this computer (D19) - unless the chat is
        trusted and holds no local-only project (D46); with the engine the user connected (their SearXNG, Tavily or
        Brave Search)."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        try:
            query = websearch.query_of(args)
        except ToolError as exc:
            return str(exc), True
        if self.secrets is None:
            return "web search is not available here", True
        engine = await websearch.engine(self.secrets, self._db)
        if engine is None:
            return ("no search engine is connected: the user adds a SearXNG address, a Tavily key or a Brave Search "
                    "key in the window's Connections"), True  # fmt: skip
        project = ctx.projects[0] if ctx.projects else None
        shown = {"query": query, "engine": websearch.ENGINES[engine]}
        if not await self.web_trusted(conversation_id):
            allowed, why = await self._ask_user(conversation_id, call["name"], shown, project, cancel)
            if not allowed:
                return why, True
        try:  # the first engine connected; when it fails, the next one
            count = args.get("count") or websearch.DEFAULT_COUNT
            answered, found = await websearch.search_any(query, store=self.secrets, db=self._db, count=count)
        except (ToolError, ValueError, TypeError) as exc:
            return str(exc), True
        return websearch.as_text(query, answered, found), False

    async def _mcp_tool(self, conversation_id: str, call: dict, ctx: ToolContext, cancel) -> tuple[str, bool]:
        """An outside MCP server's tool (9.5, D42), as its class says: a read runs; a write is asked (an answer for
        the chat covers it); a command is asked, the same input covered; network, send and destructive are asked
        every time. A trusted chat asks only a destructive one (until it reads the web, D19); a read-only chat reads
        only."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        cls = self.mcp.tool_class(call["name"])
        if cls != "read":
            settings = await self._db.acall(lambda conn: store.context_settings(conn, conversation_id))
            if settings["mode"] == "read_only":
                return READ_ONLY, True
            tainted = await self.read_the_web(conversation_id)
            if settings["mode"] != "trusted" or tainted or cls == "destructive":
                project = ctx.projects[0] if ctx.projects else None
                allowed, why = await self._ask_user(conversation_id, call["name"], args, project, cancel,
                                                    cover=not tainted)  # fmt: skip
                if not allowed:
                    return why, True
        return await self.mcp.call(call["name"], args, cancel)

    async def _trusted(self, conversation_id: str) -> bool:
        settings = await self._db.acall(lambda conn: store.context_settings(conn, conversation_id))
        return settings["mode"] == "trusted"

    async def web_trusted(self, conversation_id: str) -> bool:
        """A trusted chat reads the web without asking (the user's word 2026-10-28) - unless a local-only project is
        attached: its code could leave in a page's address or a query (D10; the user's choice 2026-11-02, D46)."""
        if not await self._trusted(conversation_id):
            return False
        return all(p.cloud_ok for p in await self._db.acall(lambda conn: _attached(conn, conversation_id)))

    async def read_the_web(self, conversation_id: str) -> bool:
        """Has the model in this chat brought in what someone else wrote - a web page, a search, a letter, an API's
        answer, another program's screen, an outside MCP tool that reaches out there? Then its writes and commands
        are asked every time (D19): that text may tell it what to do."""
        if conversation_id in self.outside:
            return True
        names = [*OUTSIDE, *(self.mcp.outside_names() if self.mcp is not None else [])]

        def read(conn) -> bool:
            found = " OR ".join("content LIKE ?" for _ in names)
            row = conn.execute(
                f"SELECT 1 FROM messages WHERE conversation_id = ? AND role = 'tool' AND ({found})",
                (conversation_id, *(f'%"name": "{n}"%' for n in names)),
            ).fetchone()
            return row is not None

        return await self._db.acall(read)

    async def _level(self, conversation_id: str) -> str:
        """An autonomous run's level of autonomy (Q22, D56); '' for anything else."""

        def level(conn) -> str:
            row = conn.execute("SELECT autonomy FROM agent_runs WHERE conversation_id = ? AND kind = 'autonomous'",
                               (conversation_id,)).fetchone()  # fmt: skip
            return (row["autonomy"] or "") if row else ""

        return await self._db.acall(level)

    async def _on_its_own(self, conversation_id: str) -> bool:
        """An autonomous run the user let go on by itself: «Авто» (autonomy "free") or «Полный» ("full")."""
        return await self._level(conversation_id) in ("free", autonomous.FULL)

    async def _allow_program(self, conversation_id: str, name: str, cancel) -> str | None:
        """A program the user has not allowed yet (33.2, D56): in «Авто» Telegram stayed refused while the user said
        «разрешаю программу» to the agent - only the settings' decision counts. The hub asks itself, once, with
        buttons; None: allowed (the caller keeps the decision there); else what the model reads."""
        if self.autonomy is None or conversation_id in self.proving:
            return f"not done: «{name}» is not among the programs the user allowed (D43), and nobody here can allow it"
        allow = tr("Разрешить «{name}»", name=name)
        question = tr("Агенту нужна программа «{name}», а её нет среди разрешённых. Разрешить агенту управлять ею? "
                      "Решение сохранится в «Программы для агента».", name=name)  # fmt: skip
        answer = await self.autonomy.ask_question(conversation_id, question, [allow, tr("Не разрешать")], cancel)
        if answer is None:
            return NOT_RUN
        if answer.strip() == allow or YES.match(answer):
            return None
        return (f"not done: the user did not allow «{name}» (they answered: {answer.strip()[:300]}) - do the task "
                "without it, or finish with an honest report")  # fmt: skip

    async def _ask_user(
        self, conversation_id: str, tool: str, args: dict, project, cancel, *, cover: bool = True
    ) -> tuple[bool, str | None]:
        """Asks in the approval queue and waits - or stops waiting when the user stops the run. (True, approval id)
        when allowed; (False, what the model should read) otherwise."""
        decision = await self._decision(conversation_id, tool, args, project, cancel, cover=cover)
        if isinstance(decision, str):
            return False, decision
        return True, decision.approval_id

    async def _decision(
        self, conversation_id: str, tool: str, args: dict, project, cancel, *, cover: bool = True
    ) -> Decision | str:
        """_ask_user's answer as it is - a write may be allowed in part (17.2): the hunks chosen, and the user's
        word with them; or what the model should read when there is no «yes». An autonomous run in «Полный» (D56)
        asks nothing: done, and kept as allowed by the level - or refused, what it never does unasked."""
        if conversation_id in self.proving:  # nobody watches a run of the proving ground to say yes (D45)
            return PROVING_NO
        if self.approvals is None:
            return "not done: the hub has no approval queue to ask the user"
        root = project.root if project else None
        unasked = await self._level(conversation_id) == autonomous.FULL
        if unasked and (never := beyond_full(tool, args, root, unsaved=self.approvals.unsaved.holds(tool, args, root))):
            return never
        asking = asyncio.create_task(self.approvals.ask(
            tool, args, conversation_id=conversation_id, project_id=project.id if project else None, root=root,
            cover=cover, unasked=unasked))  # fmt: skip
        stopping = asyncio.create_task(cancel.wait())
        await asyncio.wait({asking, stopping}, return_when=asyncio.FIRST_COMPLETED)
        stopping.cancel()
        if not asking.done():  # the user stopped the run: the request closes unanswered
            asking.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asking
            return NOT_RUN
        decision = asking.result()
        if not decision.allow:
            return f"not done: the user said no - {decision.comment}"
        return decision

    async def _consume(
        self, conversation_id, provider, model_ref, prompt, effort, cancel, tools=None, params=None
    ) -> Completion:
        completion = Completion(params=dict(params or {}))
        reasoning: list[str] = []
        content: list[str] = []
        # thinking time: from the request (or the end of a model swap) to the first visible word
        clock_from = time.monotonic()
        answer_at: float | None = None
        extra: dict[str, Any] = {"tools": tools} if tools else {}
        if params:  # only when set: a provider (or a test's stand-in) without them is called as before
            extra["params"] = params
        async for event in provider.stream_chat(model_ref, prompt, effort, cancel, **extra):
            if event.kind == "loading":
                clock_from = time.monotonic()
                await self._bus.publish(
                    "chat.loading", {"conversation_id": conversation_id, "text": event.text}, persist=False
                )
            elif event.kind == "reasoning":
                reasoning.append(event.text)
                await self._bus.publish(
                    "chat.delta",
                    {"conversation_id": conversation_id, "kind": "reasoning", "text": event.text},
                    persist=False,
                )
            elif event.kind == "content":
                answer_at = answer_at or time.monotonic()
                content.append(event.text)
                await self._bus.publish(
                    "chat.delta",
                    {"conversation_id": conversation_id, "kind": "content", "text": event.text},
                    persist=False,
                )
            elif event.kind == "end":
                completion.usage = event.usage or {}
                completion.stats = dict(event.stats or {})
                completion.stop_reason = event.stop_reason
                completion.raw = event.raw
                completion.refusal = event.refusal
                completion.tool_calls = list(event.tool_calls or [])
        completion.reasoning = "".join(reasoning)
        completion.text = "".join(content)
        completion.cancelled = cancel.is_set()
        if reasoning:
            completion.stats["think_s"] = round((answer_at or time.monotonic()) - clock_from, 1)
        return completion

    async def _save_step(self, conversation_id, model: dict, completion: Completion, client_id, run: RunTotals) -> int:
        """An answer that asks for tools: stored with its usage like any answer, but the run goes on."""
        message, cost, crossings = await self._db.acall(lambda conn: self._write(conn, conversation_id, model,
                                                                                 completion, client_id))  # fmt: skip
        run.add(completion.usage, cost)
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})
        await self._publish_crossings(model["id"], crossings)
        return message.id

    def _write(self, conn, conversation_id: str, model: dict, completion: Completion, client_id: str | None):
        cost = cost_usd(completion.usage, model["prices"])
        message = store.add_message(
            conn,
            conversation_id,
            "assistant",
            completion.blocks(),
            model_id=model["id"],
            provider_raw=completion.raw,
        )
        store.record_usage(
            conn,
            client_id=client_id,
            conversation_id=conversation_id,
            message_id=message.id,
            model_id=model["id"],
            usage=completion.usage,
            cost_usd=cost,
            estimated=bool(completion.usage.get("estimated")),
            meta={
                "stats": completion.stats,
                "stop_reason": completion.stop_reason,
                "cancelled": completion.cancelled,
                **({"params": completion.params} if completion.params else {}),
                **({"settings_version": completion.settings_version} if completion.settings_version else {}),
                **({"fallback": completion.fallback} if completion.fallback else {}),
            },
        )
        return message, cost, budgets.crossed(conn, model["id"], cost)

    async def _save(
        self, conversation_id: str, model: dict, completion: Completion, client_id: str | None, run: RunTotals
    ):
        message, cost, crossings = await self._db.acall(lambda conn: self._write(conn, conversation_id, model,
                                                                                 completion, client_id))  # fmt: skip
        run.add(completion.usage, cost)
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})
        await self._bus.publish("chat.done", run.done(conversation_id, message.id, completion))
        await self._publish_crossings(model["id"], crossings)

    async def _publish_crossings(self, model_id: str, crossings: list[tuple[str, dict]]) -> None:
        """budget.warning - a soft limit was just reached; budget.exceeded - a hard one."""
        for kind, budget in crossings:
            await self._bus.publish(f"budget.{kind}", {"model_id": model_id, "budget": budget})

    async def _save_refusal(
        self, conversation_id: str, model: dict, completion: Completion, client_id: str | None, *, final: bool = True
    ) -> float:
        """The provider declined: no assistant message joins the history, but the spent tokens are recorded. `final`:
        the run ends here (no spare to answer instead) - the window hears it as the run's error. Returns the cost."""
        cost = cost_usd(completion.usage, model["prices"])

        def write(conn):
            store.record_usage(
                conn,
                client_id=client_id,
                conversation_id=conversation_id,
                message_id=None,
                model_id=model["id"],
                usage=completion.usage,
                cost_usd=cost,
                estimated=bool(completion.usage.get("estimated")),
                meta={"stats": completion.stats, "stop_reason": completion.stop_reason},
            )
            return budgets.crossed(conn, model["id"], cost)

        crossings = await self._db.acall(write)
        if final:
            await self._bus.publish(
                "chat.error",
                {
                    "conversation_id": conversation_id,
                    "detail": completion.refusal,
                    "refusal": True,
                    "model_id": model["id"],
                    "usage": completion.usage,
                    "cost_usd": cost,
                },
            )
        await self._publish_crossings(model["id"], crossings)
        return cost


class BudgetReached(GatewayError):
    """A budget covering the model has reached its hard limit. `can_confirm`: the user may still send."""

    def __init__(self, over: list[dict], can_confirm: bool):
        super().__init__(budgets.held_back(over, "the request"))
        self.budgets = over
        self.can_confirm = can_confirm


def _stopping() -> bool:
    """This run's task is being cancelled - the hub stops."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _title_from(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LENGTH else line[: TITLE_LENGTH - 1].rstrip() + "…"


@dataclass
class Limits:
    """How far a run may go: tool steps, a monotonic deadline, money (D16, D12). A chat's are the defaults."""

    max_steps: int = field(default_factory=lambda: MAX_TOOL_STEPS)  # read when made: a chat's limit
    deadline: float | None = None
    budget_usd: float | None = None
    autonomous: bool = False  # an autonomous agent's run: nudged on to its report, pausable (D40)


@dataclass
class Outcome:
    """How a run ended: done, cancelled or error (with why), and what it took."""

    status: str = "running"
    detail: str = ""
    totals: RunTotals | None = None
    seconds: float = 0.0  # how long the run took
    limited: bool = False  # it ended at its step limit (an error for a chat, a turn's end for a goal)
    subscription: float | None = None  # Claude Code's subscription limit was reached: when it resets (0 - unsaid)


class RunTotals:
    """What all the steps of one run used and cost, for its chat.done."""

    def __init__(self) -> None:
        self.usage: dict[str, int] = {}
        self.cost = 0.0
        self.steps = 0

    def add(self, usage: dict[str, int], cost: float) -> None:
        self.steps += 1
        self.cost = round(self.cost + cost, 6)
        for key, value in usage.items():
            if isinstance(value, int) and key != "estimated":
                self.usage[key] = self.usage.get(key, 0) + value
        if usage.get("estimated"):
            self.usage["estimated"] = 1

    def done(self, conversation_id: str, message_id: int, last: Completion, *, cancelled: bool = False) -> dict:
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "usage": self.usage,
            "stats": last.stats,
            "cost_usd": self.cost,
            "steps": self.steps,
            "stop_reason": last.stop_reason,
            "cancelled": cancelled or last.cancelled,
        }


def _answer_setup(conn, conversation_id: str, model_id: str, accepts: frozenset[str]) -> tuple[str, dict, int]:
    """What the user set for this answer (D39): the system prompt in front of the conversation - the general one,
    the model's, the chat's own instructions (an agent's role comes after them, with its context) - and the answer's
    parameters: the model's, the chat's preset over them, cut to what the provider takes; and the model's settings
    version."""
    number, own = model_settings.current(conn, model_id)
    general = model_settings.current(conn, model_settings.ALL)[1]
    chat = store.context_settings(conn, conversation_id)
    system = "\n\n".join(t for t in (general.get("system_prompt"), own.get("system_prompt"), chat["instructions"]) if t)
    params = dict(own.get("params") or {})
    if chat["preset"]:
        params.update(model_settings.preset_params(chat["preset"], accepts))
    if chat["refusal_vector"]:  # the chat's own choice of Strata's refusal vector, over the model's
        params["refusal_vector"] = chat["refusal_vector"] == "on"
    task = conn.execute(
        "SELECT refusal_vector FROM agent_runs WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    if task is not None and task["refusal_vector"] is not None:  # the task's own choice wins over the model's
        params["refusal_vector"] = bool(task["refusal_vector"])
    return system, model_settings.clean_params(params, accepts), number


def _instructions(conn, conversation_id: str) -> str:
    """An agent run's role, as its profile had it when the run began; '' for a chat."""
    row = conn.execute(
        "SELECT r.profile_name, r.instructions, r.profile_id, r.kind, r.goal, r.done_when, r.autonomy, c.tools"
        " FROM agent_runs r JOIN conversations c ON c.id = r.conversation_id WHERE r.conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return ""
    text = f"You are the agent «{row['profile_name']}» of the user's hub.\n{row['instructions']}".strip()
    if row["kind"] == "autonomous":  # the goal, what "done" means, and how to work toward it (D40)
        level = {
            "each_step": "the user approves your plan and each step before it starts",
            "plan": "the user approves your plan; then you go on by yourself under the usual rights",
            "free": "you go on by yourself: acting in a program is not asked; before a write, a command, the network "
            "or sending a message the hub asks the user itself",
            autonomous.FULL: "nothing is asked - you act by yourself: writes, commands, the network, sending "
            "messages, any program of this computer the user did not deny; never ask the user for permission or to "
            "confirm. What deletes or cannot be undone, the hub's own data, passwords, payments and accepting terms "
            "are refused - say so in your report",
        }.get(row["autonomy"] or "plan", "")
        text += f"\n\n{autonomous.GUIDE}\n\nThe goal: {row['goal']}"
        hint = memory.skill_hint(conn, row["goal"])  # a skill kept from a task like this one (9.3)
        text += f"\n\n{agent_tools.context_note(conn)}"  # its folders, hosts and keys (9.4)
        text += f"\nDone means: {row['done_when']}" if row["done_when"] else ""
        text += f"\nAutonomy: {level}."
        text += f"\n\n{hint}" if hint else ""
    giving = [n for n in ("delegate_task", "delegate_tasks") if f'"{n}"' in (row["tools"] or "")]
    if giving:  # a coordinator: who it may give tasks to (D20, D50)
        others = conn.execute(
            "SELECT name, instructions FROM agent_profiles WHERE id IS NOT ? ORDER BY name", (row["profile_id"],)
        ).fetchall()
        lines = [f"- {o['name']}: {(o['instructions'].splitlines() or [''])[0][:200]}" for o in others]
        text += f"\n\nAgents you can give tasks to with {' or '.join(giving)}:\n" + ("\n".join(lines) or "- none yet")
    return text


def _sandboxed(folder: Path, conversation_id: str, projects: list, broken: dict[str, str]) -> list:
    """The chat's projects in «Песочница» (D17): a git project as its worktree; another as it is - its writes are
    refused. A git project whose worktree could not be made stays as it is too, and `broken` says why: its writes and
    commands are refused with that, never done in the project itself (D46). Blocking: git, in a thread."""
    out = []
    for project in projects:
        try:
            out.append(sandbox.ensure(folder, conversation_id, project) if sandbox.is_git(project) else project)
            broken.pop(project.id, None)
        except sandbox.SandboxError as exc:
            log.warning("no sandbox for %s: %s", project.name, exc)
            broken[project.id] = str(exc)
            out.append(project)
    return out


def _named_project(offered: list, answer: str):
    """The project the user's answer names (31.2): a button gives its name; a typed answer may hold it among other
    words («в calc») - then the only one it holds. None: no project (or which one is unclear)."""
    text = " ".join(answer.split()).casefold()
    exact = next((p for p in offered if p.name.casefold() == text), None)
    if exact is not None:
        return exact
    found = [p for p in offered if p.name.casefold() in text]
    return found[0] if len(found) == 1 else None


def _no_sandbox(project, why: str) -> str:
    return (f"not done: the sandbox copy of «{project.name}» could not be made ({why}); nothing is written or run in "
            "the project itself - tell the user")  # fmt: skip


def _attached(conn, conversation_id: str) -> list:
    found = []
    for pid in store.conversation_projects(conn, conversation_id):
        try:
            found.append(get_project(conn, pid))
        except ProjectError:
            continue
    return found


def _linked_ids(ids: set[str], all_links: list[dict[str, Any]]) -> list[str]:
    """Projects with a confirmed link to an attached one, themselves not attached (D25)."""
    return sorted(
        {link["b_id"] if link["a_id"] in ids else link["a_id"] for link in all_links
         if link["status"] == "confirmed" and (link["a_id"] in ids) != (link["b_id"] in ids)}
    )  # fmt: skip


def _to_prompt(history: list[store.MessageRow], *, cloud: bool = False, blobs: Path | None = None
               ) -> list[dict[str, Any]]:  # fmt: skip
    """The conversation as OpenAI-shape {role, content}: visible text only, reasoning left out (D14).

    An assistant turn also carries the provider's message as received, so that provider can replay its own
    blocks unchanged (Claude's thinking signatures); every other provider sends just the text. Tool calls go as
    the assistant's `tool_calls`, their results as turns of role "tool". A call left without a result (a run that
    crashed) gets one saying it did not run: providers refuse a call with no answer. A stored context message
    is rendered for the kind of model (`cloud`: local-only projects hidden).
    """
    prompt: list[dict[str, Any]] = []
    agent_calls: set[str] = set()  # Claude Code's tool calls: its own tools, not the hub's - text only for others
    for message in history:
        if (message.model_id or "").startswith(f"{CLAUDE_CODE}/"):
            agent_calls |= {b["id"] for b in message.content if b.get("type") == "tool_call"}
            text = blocks_to_prompt_text(message.content)
            if text:
                prompt.append({"role": "assistant", "content": text})
            continue
        if message.role == "tool" and agent_calls and all(b.get("id") in agent_calls for b in message.content):
            continue
        if message.role == "system":
            if any(b.get("type") == "context" for b in message.content):
                prompt.append({"role": "system", "content": context.render(message.content, cloud=cloud)})
            continue
        if message.role == "tool":
            prompt += [
                {
                    "role": "tool",
                    "tool_call_id": b["id"],
                    "name": b.get("name", ""),
                    "content": b.get("output", ""),
                    "is_error": bool(b.get("error")),
                }
                for b in message.content
                if b.get("type") == "tool_result"
            ]
            continue
        if message.role not in ("user", "assistant"):
            continue
        text = blocks_to_prompt_text(message.content)
        calls = [
            {"id": b["id"], "name": b["name"], "input": b.get("input", {})}
            for b in message.content
            if b.get("type") == "tool_call"
        ]
        if message.role == "user":  # with the files sent along: their text, their pictures
            text = attachments.prompt_text(message.content)
        if not text and message.role != "user" and not calls:
            continue
        entry: dict[str, Any] = {"role": message.role, "content": text}
        if message.role == "user" and attachments.has_images(message.content):
            entry["images"] = attachments.images(blobs, message.content)
        if calls:
            entry["tool_calls"] = calls
        if message.provider_raw is not None and message.model_id:
            entry["provider"] = message.model_id.partition("/")[0]
            entry["provider_raw"] = message.provider_raw
        prompt.append(entry)
    return _answer_open_calls(prompt)


def _answer_open_calls(prompt: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(prompt):
        out.append(entry)
        if not entry.get("tool_calls"):
            continue
        answered = set()
        for later in prompt[index + 1 :]:
            if later["role"] != "tool":
                break
            answered.add(later["tool_call_id"])
        missing = [c for c in entry["tool_calls"] if c["id"] not in answered]
        if missing and (index + 1 == len(prompt) or prompt[index + 1]["role"] != "tool"):
            out += [{"role": "tool", "tool_call_id": c["id"], "name": c["name"], "content": NOT_RUN, "is_error": True}
                    for c in missing]  # fmt: skip
    return out
