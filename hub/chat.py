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
MAX_TOOL_STEPS times. chat.done ends the run with its totals. Nothing stored is ever rewritten, and the tool list
of a conversation does not change, so Claude's thinking stays valid (D14). A cloud model reaches only projects that
allow the cloud and sees the others by name (D10); it gets no chat where a local model worked with such a project.

The model may also write files of the chat's projects (stage 5, D17): write_file and edit_file are asked in the
approval queue with a diff, unless the chat is trusted or an answer "for this chat" / a rule of the project covers
them; a read-only chat refuses them. Each write is kept as a change that can be undone (hub/changes.py). And it may
run commands (run_command, hub/commands.py) in a hub terminal: asked unless the project's whitelist or an answer for
the chat covers that very command - destructive and network commands always. A trusted chat asks nothing but a
destructive action (the user's word 2026-10-28: commands, the network and sending too) - until it reads the web
(D19): from then on it asks like any other.

A chat with a Claude Code model (stage 4, D8) runs the user's `claude` CLI in the chat's project instead
(hub/claude_code.py): same events, same stored messages; Claude Code reads the ecosystem through the hub's MCP and
keeps its own session, so the hub stores no context messages for it. Other models get its answers as text only:
its tool calls belong to Claude Code's tools, not the hub's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import (
    agent_tools,
    autonomous,
    budgets,
    cards,
    changes,
    commands,
    compact,
    computer,
    context,
    fit,
    limits,
    links,
    mail,
    mcp_client,
    memory,
    model_settings,
    notes,
    sandbox,
    store,
    web,
    websearch,
)
from .approvals import trusted_skips
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
MAX_TOOL_STEPS = 12
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
    ) -> dict:
        """Appends the user message and starts the assistant run. Returns the stored user message.

        A paid model whose budget is used up is refused before anything is stored (BudgetReached), unless every
        such budget allows asking and the user confirmed (`confirm_over_budget`); the override is logged.
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
        if model["prices"]:  # local models are free and never held back
            await self._check_budgets(model_id, confirm_over_budget, client_id)
        user_blocks = [{"type": "text", "text": text}]

        def write(conn):
            title = None
            if not store.get_title(conn, conversation_id):  # an untitled chat is named after its first message
                title = _title_from(text)
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
            task = asyncio.create_task(self._run_agent(conversation_id, model, text, cancel, client_id))
        else:
            task = asyncio.create_task(self._run(conversation_id, model, effort, cancel, client_id))
        task.add_done_callback(lambda t: t.exception())  # surface a crash to the loop's handler
        return user_msg.to_json()

    async def _check_budgets(self, model_id: str, confirmed: bool, client_id: str | None) -> None:
        over = await self._db.acall(lambda conn: budgets.reached(conn, model_id))
        if not over:
            return
        can_confirm = all(b["hard_action"] == "ask" for b in over)
        if not (confirmed and can_confirm):
            raise BudgetReached(over, can_confirm)
        await self._bus.publish("budget.overridden", {"model_id": model_id, "client_id": client_id, "budgets": over})

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
            await self._check_budgets(model_id, False, client_id)
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
        task.add_done_callback(lambda t: t.exception())

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
            self._runs.pop(conversation_id, None)
            if on_end is not None:
                if self.approvals is not None:
                    await self.approvals.expire(conversation_id)
                if self.autonomy is not None:
                    await self.autonomy.expire_all(conversation_id)  # its questions too: nobody waits for them now
                await on_end(outcome)

    async def _run_agent(self, conversation_id: str, model: dict, text: str, cancel: asyncio.Event, client_id):
        """A turn of Claude Code in the chat's projects - those a cloud model may reach (D10) - or, with none
        attached, in a folder of the chat's own."""
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
            await self.claude_code.run(conversation_id, model, text, usable, cancel, client_id)
        except GatewayError as exc:
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": str(exc),
                                                   "model_id": model["id"]})  # fmt: skip
        except Exception:
            log.exception("claude code turn for %s crashed", conversation_id)
            await self._bus.publish("chat.error", {"conversation_id": conversation_id, "detail": "internal error",
                                                   "model_id": model["id"]})  # fmt: skip
        finally:
            self._runs.pop(conversation_id, None)
            if self.approvals is not None:
                await self.approvals.expire(conversation_id)  # nobody waits for its open requests now
            if self.autonomy is not None:
                await self.autonomy.expire_all(conversation_id)

    async def _steps(self, conversation_id, model: dict, effort: str, cancel, client_id, limits: Limits, outcome):
        attached = await self._db.acall(lambda conn: _attached(conn, conversation_id))

        async def setup(model: dict):
            """What answering with this model takes: its provider, the projects it may see, the system prompt and
            the answer's parameters the user set (D39)."""
            provider, model_ref = self._gateway.resolve(model["id"])
            await self._use_limits(model["id"])  # the tools' portions of this model (D22, D39)
            cloud = model.get("locality") == "cloud"
            visible = [p for p in attached if p.cloud_ok or not cloud]
            mode = (await self._db.acall(lambda conn: store.context_settings(conn, conversation_id)))["mode"]
            if mode == "sandbox" and self.sandboxes is not None:  # its tools see each git project's worktree
                visible = await asyncio.to_thread(_sandboxed, self.sandboxes, conversation_id, visible)
            ctx = ToolContext(visible, terminals=self.terminals, processes=self.processes,
                              symbols=self.symbols)  # fmt: skip
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
            prompt = _to_prompt(history, cloud=cloud)
            if system:  # in front, the same in every request: the fixed part of the prompt (cache, D15)
                prompt = [{"role": "system", "content": system}, *prompt]
            # the tool list never changes within a conversation (D14), and every model has it from the first message:
            # the web tools are for any chat, with projects or without (D19) - the definitions it was first sent,
            # even after the hub's tools changed
            if tools is None:  # a new chat gets the outside MCP servers' tools ready now too (D42) - sized to its
                # model's window (hub/compact.py; projects may come later in a chat: their tools stay)
                current = compact.for_model([*CHAT_TOOLS, *(self.mcp.definitions() if self.mcp is not None else [])],
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
                    await self._bus.publish("chat.done", run.done(conversation_id, message_id, completion))
                    return
                continue
            await self._answer_tools(conversation_id, completion.tool_calls, ctx, cancel, cloud=cloud,
                                     counted=limits.autonomous)  # fmt: skip
            switch = self.autonomy.take_switch(conversation_id) if self.autonomy is not None else None
            if switch:  # the planner made the plan: the run's own model carries it out
                found = await self._db.acall(lambda conn, m=switch: store.get_model(conn, m))
                if found is not None and found["enabled"]:
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
            if model["prices"]:
                over = await self._db.acall(lambda conn, m=model["id"]: budgets.reached(conn, m))
                if over:
                    raise GatewayError("the budget ran out while the model was working; send again to go on")

    async def _hub_note(self, conversation_id: str, text: str) -> None:
        """A word from the hub to the model between its steps (D40): stored as a user turn, marked as the hub's."""
        message = await self._db.acall(lambda conn: store.add_message(conn, conversation_id, "user",
                                                                       [{"type": "text", "text": text,
                                                                         "hub": True}]))  # fmt: skip
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
        log.info("chat %s: %s failed (%s), the spare %s answers", conversation_id, model["id"], reason, spare["id"])
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
        for call in calls:
            if (refuse and call["name"] not in let) or cancel.is_set():
                results.append(tool_result_block(call["id"], call["name"], refuse or NOT_RUN, True))
                continue
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
            elif call["name"] in changes.WRITE_TOOL_NAMES:
                output, error = await self._write_file(conversation_id, call, ctx, cancel)
            elif call["name"] == commands.RUN_COMMAND["name"]:
                output, error = await self._run_command(conversation_id, call, ctx, cancel)
            elif call["name"] == web.WEB_FETCH["name"]:
                output, error = await self._web_fetch(conversation_id, call, ctx, cancel)
            elif call["name"] == websearch.WEB_SEARCH["name"]:
                output, error = await self._web_search(conversation_id, call, ctx, cancel)
            elif call["name"] in agent_tools.TOOL_NAMES and self.toolbox is not None:  # beyond projects (9.4)

                async def ask(tool, data, cover, call=call):
                    allowed, said = await self._ask_user(conversation_id, tool, data, None, cancel, cover=cover)
                    return None if allowed else said

                output, error = await self.toolbox.handle(conversation_id, call, cancel, ask)
            elif call["name"] in computer.TOOL_NAMES and self.computer is not None:  # the desktop (9.6, D41)

                async def ask(tool, shown, cover, call=call):
                    if tool == "screen_act" and await self._on_its_own(conversation_id):
                        return None  # «Сам»: in an allowed program without a card; sending is still asked
                    allowed, said = await self._ask_user(conversation_id, tool, shown, None, cancel, cover=cover)
                    return None if allowed else said

                output, error = await self.computer.handle(conversation_id, call, cancel, ask)
            elif call["name"] in mail.TOOL_NAMES and self.mail is not None:  # the mailbox (9.4b, Q21)

                async def ask(tool, shown, cover, call=call):
                    allowed, said = await self._ask_user(conversation_id, tool, shown, None, cancel, cover=cover)
                    return None if allowed else said

                output, error = await self.mail.handle(conversation_id, call, cancel, ask, cloud=cloud)
            elif outside:  # an outside MCP server's (9.5, D42)
                output, error = await self._mcp_tool(conversation_id, call, ctx, cancel)
            elif own and self.autonomy is not None:  # an autonomous run's plan, steps and memory (D40)
                output, error = await self.autonomy.handle(conversation_id, call, cancel, cloud=cloud)
            elif call["name"] == "delegate_task":  # a coordinator's (D20)
                if self.agents is None:
                    output, error = "agents are not available here", True
                else:
                    args = call["input"] if isinstance(call["input"], dict) else {}
                    output, error = await self.agents.delegate(conversation_id, args, cancel)
            else:
                output, error = await run_tool(call["name"], call["input"], ctx)
            results.append(tool_result_block(call["id"], call["name"], output, error))
            if counted and self.autonomy is not None:
                await self.autonomy.acted(conversation_id, call["name"])
        message = await self._db.acall(lambda conn: store.add_message(conn, conversation_id, "tool", results))
        await self._bus.publish("chat.message", {"conversation_id": conversation_id, "message": message.to_json()})

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
        if settings["mode"] == "sandbox":  # into the worktree, unasked: the user reviews the diff (D17)
            return await self._sandbox_write(conversation_id, project, planned)
        approval_id = None
        tainted = await self._read_the_web(conversation_id)
        if settings["mode"] != "trusted" or tainted:  # after reading the web, trust does not count (D19)
            allowed, approval_id = await self._ask_user(conversation_id, call["name"], args, project, cancel,
                                                        cover=not tainted)  # fmt: skip
            if not allowed:
                return approval_id, True
        if self.snapshots is None:
            return "not done: the hub keeps no snapshots here, so the change could not be undone", True
        try:
            hashes = await asyncio.to_thread(changes.apply, planned, self.snapshots)
        except changes.WriteError as exc:
            return str(exc), True
        except OSError as exc:
            return f"could not write {planned.path}: {exc.strerror or exc}", True
        change = await self._db.acall(lambda conn: changes.record(conn, project.id, planned, hashes, call["name"],
                                                                   conversation_id, approval_id))  # fmt: skip
        await self._bus.publish("file.changed", {"change": change, "project_id": project.id}, project_id=project.id)
        return f"{changes.summary(planned)} (change {change['id']}; the user can undo it)", False

    async def _sandbox_write(self, conversation_id: str, project, planned) -> tuple[str, bool]:
        if self.sandboxes is None or not project.root.is_relative_to(self.sandboxes):
            return (f"not done: «{project.name}» is not a git repository, and the sandbox needs one - the user may "
                    "switch the chat to «Спрашивать»"), True  # fmt: skip
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
        tainted = await self._read_the_web(conversation_id)
        if settings["mode"] != "trusted" or tainted or not trusted_skips(call["name"], args):
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
        the chat is trusted."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        try:
            url = web.check_url(args.get("url"))
        except ToolError as exc:
            return str(exc), True
        if web.site(url) not in web.ALLOWED_SITES and not await self._trusted(conversation_id):
            project = ctx.projects[0] if ctx.projects else None  # the chat's project keeps "always" answers
            allowed, why = await self._ask_user(conversation_id, call["name"], args, project, cancel)
            if not allowed:
                return why, True
        try:
            return await web.fetch(args), False
        except ToolError as exc:
            return str(exc), True

    async def _web_search(self, conversation_id: str, call: dict, ctx: ToolContext, cancel) -> tuple[str, bool]:
        """A search the model wants: asked every time - the query leaves this computer (D19) - unless the chat is
        trusted; with the engine the user connected (their SearXNG, Tavily or Brave Search)."""
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
        if not await self._trusted(conversation_id):
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
            tainted = await self._read_the_web(conversation_id)
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

    async def _read_the_web(self, conversation_id: str) -> bool:
        """Has the model in this chat read a web page, searched, or used an outside MCP tool that goes out there?
        Then its writes and commands are asked every time (D19)."""
        names = ["web_fetch", "web_search", *(self.mcp.network_names() if self.mcp is not None else [])]

        def read(conn) -> bool:
            found = " OR ".join("content LIKE ?" for _ in names)
            row = conn.execute(
                f"SELECT 1 FROM messages WHERE conversation_id = ? AND role = 'tool' AND ({found})",
                (conversation_id, *(f'%"name": "{n}"%' for n in names)),
            ).fetchone()
            return row is not None

        return await self._db.acall(read)

    async def _on_its_own(self, conversation_id: str) -> bool:
        """An autonomous run the user let go on by itself («Сам», autonomy "free")."""

        def level(conn) -> str:
            row = conn.execute("SELECT autonomy FROM agent_runs WHERE conversation_id = ? AND kind = 'autonomous'",
                               (conversation_id,)).fetchone()  # fmt: skip
            return row["autonomy"] if row else ""

        return await self._db.acall(level) == "free"

    async def _ask_user(
        self, conversation_id: str, tool: str, args: dict, project, cancel, *, cover: bool = True
    ) -> tuple[bool, str | None]:
        """Asks in the approval queue and waits - or stops waiting when the user stops the run. (True, approval id)
        when allowed; (False, what the model should read) otherwise."""
        if self.approvals is None:
            return False, "not done: the hub has no approval queue to ask the user"
        asking = asyncio.create_task(self.approvals.ask(
            tool, args, conversation_id=conversation_id, project_id=project.id if project else None,
            root=project.root if project else None, cover=cover))  # fmt: skip
        stopping = asyncio.create_task(cancel.wait())
        await asyncio.wait({asking, stopping}, return_when=asyncio.FIRST_COMPLETED)
        stopping.cancel()
        if not asking.done():  # the user stopped the run: the request closes unanswered
            asking.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asking
            return False, NOT_RUN
        decision = asking.result()
        if not decision.allow:
            return False, f"not done: the user said no - {decision.comment}"
        return True, decision.approval_id

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
        names = ", ".join(f"{b['scope_ref'] or 'all paid models'} per {b['period']}" for b in over)
        super().__init__(f"budget reached: {names}")
        self.budgets = over
        self.can_confirm = can_confirm


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
            "free": "you go on by yourself under the usual rights",
        }.get(row["autonomy"] or "plan", "")
        text += f"\n\n{autonomous.GUIDE}\n\nThe goal: {row['goal']}"
        hint = memory.skill_hint(conn, row["goal"])  # a skill kept from a task like this one (9.3)
        text += f"\n\n{agent_tools.context_note(conn)}"  # its folders, hosts and keys (9.4)
        text += f"\nDone means: {row['done_when']}" if row["done_when"] else ""
        text += f"\nAutonomy: {level}."
        text += f"\n\n{hint}" if hint else ""
    if '"delegate_task"' in (row["tools"] or ""):  # a coordinator: who it may give tasks to (D20)
        others = conn.execute(
            "SELECT name, instructions FROM agent_profiles WHERE id IS NOT ? ORDER BY name", (row["profile_id"],)
        ).fetchall()
        lines = [f"- {o['name']}: {(o['instructions'].splitlines() or [''])[0][:200]}" for o in others]
        text += "\n\nAgents you can give tasks to with delegate_task:\n" + ("\n".join(lines) or "- none yet")
    return text


def _sandboxed(folder: Path, conversation_id: str, projects: list) -> list:
    """The chat's projects in «Песочница» (D17): a git project as its worktree; another as it is - its writes are
    refused (blocking: git, in a thread)."""
    out = []
    for project in projects:
        try:
            out.append(sandbox.ensure(folder, conversation_id, project) if sandbox.is_git(project) else project)
        except sandbox.SandboxError as exc:
            log.warning("no sandbox for %s: %s", project.name, exc)
            out.append(project)
    return out


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


def _to_prompt(history: list[store.MessageRow], *, cloud: bool = False) -> list[dict[str, Any]]:
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
        if not text and message.role != "user" and not calls:
            continue
        entry: dict[str, Any] = {"role": message.role, "content": text}
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
