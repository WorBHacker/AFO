"""The hub's web application: database, event bus and API in one process (D28)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Route

from common import __version__

from . import autostart, mcp_server
from .agents import AgentRunner
from .api import (
    advice,
    agents,
    approvals,
    budgets,
    cards,
    changes,
    claude_code,
    connections,
    conversations,
    editor,
    events_ws,
    links,
    llama,
    model_settings,
    models,
    notes,
    openai,
    processes,
    programs,
    projects,
    stats,
    system,
    terminals,
)
from .api import (
    autostart as autostart_api,
)
from .approvals import ApprovalQueue
from .auth import ensure_app_token
from .autonomous import Autonomy
from .backup import DailyBackup
from .chat import ChatService
from .claude_code import ClaudeCodeRunner
from .connections import ConnectionLock
from .db import Database, migrate
from .editor import Editor
from .events import EventBus
from .gateway.registry import build_default_gateway
from .processes import ProcessMonitor
from .programs import ProgramCatalog
from .projects import list_projects
from .secrets import SecretStore, default_secret_store
from .security import LocalOnlyMiddleware
from .settings import HubSettings
from .system_status import StatusMonitor
from .telegram import TelegramBot
from .terminals import TerminalManager
from .watch import ProjectWatcher

log = logging.getLogger("hub")


def create_app(settings: HubSettings, secret_store: SecretStore | None = None) -> FastAPI:
    """`secret_store` defaults to the OS store (Windows Credential Manager); tests pass their own."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        paths = settings.paths.ensure()
        db = Database(paths.db)
        applied = db.call(migrate)
        if applied:
            log.info("database %s: applied migrations %s", paths.db, applied)
        token_file = db.call(lambda conn: ensure_app_token(conn, paths.tokens))
        bus = EventBus(db)
        secrets_store = secret_store if secret_store is not None else default_secret_store()
        gateway = build_default_gateway(db, settings.llama_swap_url, secrets_store)
        app.state.settings = settings
        app.state.secrets = secrets_store
        app.state.lock = ConnectionLock(secrets_store)  # the connections' password (D13)
        app.state.run_store = autostart.default_run_store()  # the hub at Windows login (Q8)
        app.state.db = db
        app.state.bus = bus
        app.state.gateway = gateway
        app.state.chat = ChatService(db, bus, gateway)
        app.state.chat.secrets = secrets_store
        app.state.editor = app.state.chat.editor = Editor(db, bus)  # VS Code's open file and commands (stage 6.6)
        app.state.approvals = ApprovalQueue(db, bus)
        # Claude Code (D8, D18): the user's CLI in a chat's project, with the hub's MCP and its approval queue
        hub_url = f"http://{settings.host}:{settings.port}"
        app.state.claude_code = ClaudeCodeRunner(db, bus, paths, lambda: hub_url)
        app.state.chat.claude_code, app.state.chat.approvals = app.state.claude_code, app.state.approvals
        app.state.chat.snapshots = paths.snapshots  # the files before and after each write (stage 5, D17)
        app.state.autonomy = app.state.chat.autonomy = Autonomy(db, bus, app.state.approvals)  # D40
        app.state.agents = AgentRunner(db, bus, app.state.chat)  # the hub's own agents (stage 5, D16)
        app.state.chat.agents = app.state.agents  # a coordinator gives tasks to other agents (D20)
        # approvals from the phone (D21): talks to Telegram only once the user has set the bot's token
        app.state.telegram = TelegramBot(db, bus, app.state.approvals, secrets_store)
        app.state.status = StatusMonitor(bus, settings.llama_swap_url, settings.status_interval_s)
        app.state.started_monotonic = time.monotonic()
        app.state.card_drafts = set()  # projects whose card a model is drafting now
        # the hub's own data may sit inside a project (this repository): its writes are not project changes
        app.state.watcher = ProjectWatcher(bus, ignore=[paths.root])
        app.state.processes = ProcessMonitor(bus, db, settings.process_interval_s)
        app.state.programs = ProgramCatalog(bus, db, settings.programs_interval_s)
        app.state.backup = DailyBackup(paths.db, paths.backups, settings.backup_interval_s)
        app.state.terminals = TerminalManager(bus, db, paths.terminals)
        app.state.chat.terminals, app.state.chat.processes = app.state.terminals, app.state.processes
        app.state.symbols = app.state.chat.symbols  # one index for the window's queries and the models' tools
        await bus.publish("hub.started", {"version": __version__, "migrations_applied": applied})
        # the catalog syncs in the background: the hub answers at once even if llama-swap or the network is slow
        catalog = app.state.initial_sync = asyncio.create_task(_initial_sync(app))
        app.state.status.start()
        app.state.watcher.start(db.call(list_projects))
        app.state.processes.start()
        app.state.programs.start()
        app.state.backup.start()
        await app.state.terminals.start()
        expired = await app.state.approvals.start()
        if expired:
            log.info("%s approval requests left open by the last hub expired", expired)
        app.state.telegram.start()
        if await app.state.autonomy.expire_all():  # questions the last hub left open: the agents ask again
            log.info("open questions of autonomous runs expired")
        resumed = await app.state.agents.start()
        if resumed:
            log.info("agent runs going on after a restart: %s", resumed)
        log.info("hub ready, app token in %s", token_file)
        try:
            # the MCP app is mounted, not run: its session manager lives in the hub's lifespan
            async with mcp.session_manager.run():
                yield
        finally:
            catalog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await catalog
            await app.state.status.stop()
            await app.state.watcher.stop()
            await app.state.processes.stop()
            await app.state.programs.stop()
            await app.state.backup.stop()
            await app.state.telegram.stop()
            await app.state.claude_code.stop()
            await app.state.terminals.stop()
            await bus.publish("hub.stopping", {})
            db.close()
            log.info("hub stopped")

    # the interactive docs pages are for browsers, which this hub does not serve (D31)
    app = FastAPI(
        title="LocalChat hub", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(
        LocalOnlyMiddleware, allowed_hostnames=settings.allowed_hostnames, allowed_origins=settings.allowed_origins
    )
    # set by `python -m hub` once the server object exists; POST /v1/shutdown calls it
    app.state.request_shutdown = None
    app.include_router(system.router)
    app.include_router(events_ws.router)
    app.include_router(models.router)
    app.include_router(model_settings.router)
    app.include_router(llama.router)
    app.include_router(conversations.router)
    app.include_router(connections.router)
    app.include_router(autostart_api.router)
    app.include_router(editor.router)
    app.include_router(budgets.router)
    app.include_router(projects.router)
    app.include_router(programs.router)
    app.include_router(processes.router)
    app.include_router(terminals.router)
    app.include_router(cards.router)
    app.include_router(notes.router)
    app.include_router(links.router)
    app.include_router(approvals.router)
    app.include_router(claude_code.router)
    app.include_router(changes.router)
    app.include_router(agents.router)
    app.include_router(stats.router)
    app.include_router(advice.router)
    app.include_router(openai.router)
    # MCP for Cline, Claude Code and the like (D32): /mcp, with a client token
    mcp, mcp_app = mcp_server.build(app)
    app.router.routes.append(Route("/mcp", endpoint=mcp_app))
    return app


async def _initial_sync(app: FastAPI) -> None:
    settings = app.state.settings
    if settings.manage_llama_swap:  # llama-swap down: the hub starts it first (Q8), then reads its models
        try:
            wanted = await app.state.db.acall(autostart.llama_swap_wanted)
            started = await autostart.ensure_llama_swap(settings.llama_swap_url, autostart.llama_swap_script(), wanted,
                                                        settings.paths.logs / "llama-swap.out.log")  # fmt: skip
            log.info("llama-swap: %s", started)
            if started in ("started", "slow"):
                await app.state.bus.publish("llama_swap.started", {"ready": started == "started"})
        except Exception:
            log.exception("starting llama-swap failed")
    try:
        result = await app.state.gateway.sync_catalog()  # best effort: llama-swap or the network may be down
    except Exception:
        log.exception("startup catalog sync failed")
        return
    log.info("model catalog: %s", result)
    await app.state.bus.publish("models.synced", result)
