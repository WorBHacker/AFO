"""The hub's web application: database, event bus and API in one process (D28)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI
from starlette.routing import Route

from common import __version__

from . import autostart, language, mcp_server
from .agent_tools import AgentToolbox
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
    goals,
    gpu,
    history,
    install,
    links,
    llama,
    mail,
    mcp_servers,
    memory,
    model_settings,
    models,
    notes,
    openai,
    portable,
    processes,
    programs,
    projects,
    proving,
    release,
    sandbox,
    schedules,
    setup,
    stats,
    support,
    system,
    terminals,
    tuning,
    visual,
)
from .api import (
    autostart as autostart_api,
)
from .api import (
    language as language_api,
)
from .api.model_settings import run_check
from .approvals import ApprovalQueue
from .auth import ensure_app_token, load_token_files
from .autonomous import Autonomy
from .backup import DailyBackup, migrate_keeping_a_copy
from .chat import ChatService
from .claude_code import ClaudeCodeRunner
from .completion import Completions
from .computer import Computer, vision_eyes
from .connections import ConnectionLock
from .connections import kept as kept_secrets
from .db import Database
from .editor import Editor
from .events import EventBus
from .gateway.registry import build_default_gateway
from .goals import Goals
from .gpu_prefs import Prefs as GpuPrefs
from .gpu_prefs import Watcher as GpuWatcher
from .history import History
from .judge import Judge
from .links import Rescanner
from .mail import MailTools
from .mcp_client import McpServers
from .model_install import Installer
from .model_scan import Scanner
from .processes import ProcessMonitor
from .programs import ProgramCatalog
from .projects import keep_private, list_projects, set_path
from .proving import ProvingGround
from .release import Releases
from .resources import FETCHING, Resources
from .retention import Keeper
from .ripgrep import Fetcher
from .sandbox import orphans as sandbox_orphans
from .schedule import Scheduler
from .secrets import Masking, SecretStore, default_secret_store
from .security import LocalOnlyMiddleware
from .settings import HubSettings
from .setup import Setup
from .system_status import StatusMonitor
from .telegram import TelegramBot
from .terminals import TerminalManager
from .tuning import Tuner
from .visual import Visual
from .watch import ProjectWatcher

log = logging.getLogger("hub")


def create_app(settings: HubSettings, secret_store: SecretStore | None = None) -> FastAPI:
    """`secret_store` defaults to the OS store (Windows Credential Manager); tests pass their own."""
    requested = settings.llama_swap_url  # llama-swap's address as asked; its port may move at each start (39.2)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal settings
        paths = settings.paths.ensure()
        keep_private(paths.root)  # the data folder is never served through a project, even LocalChat's own (D46)
        db = Database(paths.db)
        applied = migrate_keeping_a_copy(db, paths.backups)  # a copy of the database first (D46)
        if applied:
            log.info("database %s: applied migrations %s", paths.db, applied)
        token_file = db.call(lambda conn: ensure_app_token(conn, paths.tokens))
        load_token_files(paths.tokens)  # masked in all a model sees, by value (D46)
        db.call(autostart.load_choices)  # llama-swap's config and its start, as chosen in the window (10.3)
        # where the hub moved off a port another program holds - it stays there next time (43.9)
        db.call(lambda conn: autostart.keep_hub_port(conn, settings.wanted_port, settings.port))
        if settings.move_llama_swap:  # another program on its port: AFO's own llama-swap on a free one (39.2) -
            # before all that talks to llama-swap is built with its address
            settings = replace(settings, llama_swap_url=await autostart.llama_swap_url(db, requested))
        if await autostart.find_running(db, settings.llama_swap_url):  # one the person runs already (10.4)
            log.info("llama-swap's paths: those of the one running")
        log.info("language: %s", db.call(language.load))  # what the hub writes for people (10.5)
        bus = EventBus(db)
        # what the hub keeps is masked by its value in all a model sees and in the logs, from the start (22.1)
        secrets_store = Masking(secret_store if secret_store is not None else default_secret_store())
        db.call(lambda conn: secrets_store.load(kept_secrets(conn)))
        gateway = build_default_gateway(db, settings.llama_swap_url, secrets_store)
        app.state.settings = settings
        app.state.secrets = secrets_store
        app.state.lock = ConnectionLock(secrets_store)  # the connections' password (D13)
        app.state.run_store = autostart.default_run_store()  # the hub at Windows login (Q8)
        autostart.renamed(app.state.run_store, [autostart.default_run_store(name)
                                                for name in autostart.OLD_RUN_VALUES])  # 10.4b, 43.4  # fmt: skip
        app.state.db = db
        app.state.bus = bus
        app.state.gateway = gateway
        app.state.chat = ChatService(db, bus, gateway)
        app.state.chat.secrets = secrets_store
        app.state.editor = app.state.chat.editor = Editor(db, bus)  # VS Code's open file and commands (stage 6.6)
        app.state.approvals = ApprovalQueue(db, bus)
        app.state.completions = Completions(db, gateway, settings.llama_swap_url)  # the editor's grey text (D47)
        # Claude Code (D8, D18): the user's CLI in a chat's project, with the hub's MCP and its approval queue
        app.state.hub_url = hub_url = f"http://{settings.host}:{settings.port}"  # where it listens (43.9)
        app.state.claude_code = ClaudeCodeRunner(db, bus, paths, lambda: hub_url)
        app.state.chat.claude_code, app.state.chat.approvals = app.state.claude_code, app.state.approvals
        app.state.chat.snapshots = paths.snapshots  # the files before and after each write (stage 5, D17)
        app.state.chat.sandboxes = paths.root / "worktrees"  # «Песочница» chats' worktrees (D17, 9.4b)
        app.state.chat.blobs = paths.blobs  # files and pictures sent with messages (2026-10-29)
        app.state.autonomy = app.state.chat.autonomy = Autonomy(db, bus, app.state.approvals)  # D40
        app.state.toolbox = app.state.chat.toolbox = AgentToolbox(db, bus, app.state.approvals, paths.root / "sandbox",
                                                                  secrets=secrets_store)  # fmt: skip
        app.state.mcp = app.state.chat.mcp = McpServers(db, bus, secrets_store)  # outside MCP servers (9.5, D42)
        app.state.computer = app.state.chat.computer = Computer(  # the agent's hands on the desktop (9.6, D41)
            db,
            bus,
            paths.root / "computer",
            see=vision_eyes(db, gateway),
            still_running=lambda run: run in app.state.chat.busy,
            watched=lambda: getattr(app.state, "app_windows", 0) > 0,  # the window draws the frame and «stop»
        )
        app.state.agents = AgentRunner(db, bus, app.state.chat)  # the hub's own agents (stage 5, D16)
        app.state.chat.agents = app.state.agents  # a coordinator gives tasks to other agents (D20)
        app.state.judge = Judge(db, bus, gateway, paths.snapshots)  # checks goals and agents' reports (D44, 11.2)
        app.state.goals = app.state.chat.goals = Goals(db, bus, app.state.chat, app.state.judge)  # D44, 11.1
        app.state.agents.goals = app.state.goals  # a coordinator's tasks are checked as goals are (16.3, D50)
        app.state.proving = ProvingGround(db, bus, app.state.chat, app.state.goals, paths.root / "proving")  # D45
        app.state.autonomy.judge = app.state.judge
        # approvals from the phone (D21): talks to Telegram only once the user has set the bot's token
        app.state.mail = app.state.chat.mail = MailTools(db, secrets_store)  # the user's mailbox (9.4b, Q21)
        app.state.scheduler = Scheduler(db, bus, app.state.agents,  # goals on a schedule and on events (9.7)
                                        tick_s=settings.schedule_tick_s or 20.0, mail=app.state.mail)  # fmt: skip
        app.state.telegram = TelegramBot(db, bus, app.state.approvals, secrets_store,
                                         quiet=app.state.scheduler.quiet_now)  # fmt: skip
        # another program let llama-swap's port go: llama-swap starts, as the hub's start does it (23.4)
        app.state.status = StatusMonitor(bus, settings.llama_swap_url, settings.status_interval_s,
                                         freed=lambda: _initial_sync(app))  # fmt: skip
        app.state.installer = Installer(
            db,
            bus,
            gateway,
            secrets_store,
            llama_swap_url=settings.llama_swap_url,
            status=lambda: app.state.status.latest or app.state.status.machine,
            computer=paths.computer,
            answers=paths.hf_answers,
        )  # local models (10.1, D37)
        app.state.status.on_machine = app.state.installer.note_machine  # computer.json (35.2, D58)
        app.state.chat.memory_now = app.state.installer.memory_now  # «сейчас свободно…» while a model loads (38.2)
        app.state.installer.trial = lambda model, client: run_check(app.state, model, client)
        app.state.scanner = Scanner(db, bus)  # models already on the drives (15.1)
        # launch settings by measurement (15.2): after an install, after llama.cpp's update, on the window's word
        app.state.tuner = Tuner(db, bus, app.state.chat, url=settings.llama_swap_url, space=app.state.installer.space,
                                gpu=lambda: (app.state.status.latest or {}).get("gpu"),
                                installing=app.state.installer.busy,
                                drafts=app.state.installer.draft_paths)  # fmt: skip
        # guessing ahead (42.5): after an install its draft model comes by itself, then the tuning measures with it;
        # a draft that gave nothing goes once no model uses it
        app.state.installer.on_done = lambda model_id: app.state.installer.speed_up(model_id, automatic=True)
        app.state.installer.on_speed_up = lambda model_id, auto: app.state.tuner.request(model_id, auto=auto)
        app.state.tuner.on_tuned = app.state.installer.tuned
        app.state.installer.on_renamed = app.state.tuner.rename  # what was measured stays the model's (36.1)
        # programs on the graphics card go to the integrated graphics, its memory stays the models' (43.6)
        app.state.gpu = GpuWatcher(db, bus, GpuPrefs(db), interval_s=settings.gpu_interval_s)
        # one at a time on the graphics card (34.4): a trial waits while tuning measures, and for a chat's answer
        app.state.installer.tuning = lambda: app.state.tuner.job.get("status") in ("measuring", "checking")
        app.state.installer.chat_busy = lambda: bool(app.state.chat.busy)
        app.state.releases = Releases(db, bus, paths.root / "updates")  # AFO's own updates (10.4d)
        app.state.setup = Setup(
            db, bus, gateway, app.state.installer, llama_swap_url=settings.llama_swap_url, logs=paths.logs
        )  # the first-run wizard (10.2)
        app.state.setup.on_updated = lambda: app.state.tuner.request_all(auto=True)  # a newer llama.cpp: measured anew
        app.state.installer.engine_coming = lambda: app.state.setup.job.get("status") in FETCHING  # 39.3
        # what local models need, on the disk: AFO's own engine missing - fetched at the start and when the window
        # comes (39.1); the person's own llama-swap never touched
        app.state.resources = Resources(
            db, app.state.setup, url=settings.llama_swap_url, enabled=settings.fetch_engines
        )
        app.state.visual = Visual(db, bus, llama_swap_url=settings.llama_swap_url, media=paths.media,
                                  machine=lambda: app.state.installer.machine())  # pictures (29.4, D55)  # fmt: skip
        app.state.chat.visual = app.state.visual  # a chat's model draws too (29.6)
        app.state.visual.blobs = paths.blobs  # a chat's pictures: sources for «По картинке» (29.6б)
        if (swap := app.state.gateway.provider("llama-swap")) is not None:  # a video holds the card (29.8а)
            swap.gate = app.state.visual.gpu_gate
        app.state.started_monotonic = time.monotonic()
        app.state.card_drafts = set()  # projects whose card a model is drafting now
        # the hub's own data may sit inside a project (this repository): its writes are not project changes
        app.state.history = History(db, bus, paths.snapshots)  # each new version of a project's file (stage 26)
        app.state.watcher = ProjectWatcher(
            bus, ignore=[paths.root], listed=lambda: db.acall(list_projects),
            relocate=lambda pid, folder: db.acall(lambda conn: set_path(conn, pid, folder)),
            check_s=settings.folder_check_s, keep=app.state.history.keep,
        )  # fmt: skip
        app.state.processes = ProcessMonitor(bus, db, settings.process_interval_s)
        app.state.rescanner = Rescanner(bus, db, app.state.processes, settings.links_quiet_s)
        app.state.ripgrep = Fetcher(bus, enabled=settings.fetch_ripgrep)
        app.state.programs = ProgramCatalog(bus, db, settings.programs_interval_s)
        app.state.backup = DailyBackup(paths.db, paths.backups, settings.backup_interval_s)
        app.state.keeper = Keeper(db, paths.root)  # what the hub keeps on disk within D30's limits
        app.state.terminals = TerminalManager(bus, db, paths.terminals)
        app.state.chat.terminals, app.state.chat.processes = app.state.terminals, app.state.processes
        app.state.symbols = app.state.chat.symbols  # one index for the window's queries and the models' tools
        await bus.publish("hub.started", {"version": __version__, "migrations_applied": applied})
        # the catalog syncs in the background: the hub answers at once even if llama-swap or the network is slow
        catalog = app.state.initial_sync = asyncio.create_task(_initial_sync(app))
        app.state.status.start()
        app.state.watcher.start(db.call(list_projects))
        app.state.processes.start()
        app.state.rescanner.start()
        app.state.ripgrep.start()
        app.state.programs.start()
        app.state.backup.start()
        await app.state.terminals.start(prune=False)  # its logs are pruned with the rest (hub/retention.py)
        app.state.keeper.start()
        expired = await app.state.approvals.start()
        if expired:
            log.info("%s approval requests left open by the last hub expired", expired)
        app.state.telegram.start()
        await app.state.scanner.start()  # what the last search found
        await app.state.tuner.start()
        app.state.gpu.start()  # the graphics card for models (43.6): not in tests
        await app.state.releases.start()  # says how the last update went, then checks now and then
        if await app.state.installer.start():
            log.info("model installs left going by the last hub wait for «Продолжить»")
        if await app.state.visual.start():
            log.info("pictures left going by the last hub failed")
        app.state.scheduler.start(clock=settings.schedule_tick_s > 0)
        servers = asyncio.create_task(app.state.mcp.start())  # in the background: a slow server holds nothing up
        if await app.state.autonomy.expire_all():  # questions the last hub left open: the agents ask again
            log.info("open questions of autonomous runs expired")
        resumed = await app.state.agents.start()
        if resumed:
            log.info("agent runs going on after a restart: %s", resumed)
        if await app.state.proving.start():  # before the goals go on: the proving ground's do not
            log.info("runs of the proving ground left going by the last hub are stopped")
        goals_on = asyncio.create_task(_resume_goals(app))  # once the catalog is read: their models must be known
        sweep = asyncio.create_task(_sweep_sandboxes(app))  # sandboxes of chats gone while the hub was off (D46)
        log.info("hub ready, app token in %s", token_file)
        try:
            # the MCP app is mounted, not run: its session manager lives in the hub's lifespan
            async with mcp.session_manager.run():
                yield
        finally:
            catalog.cancel()
            servers.cancel()
            goals_on.cancel()
            sweep.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await servers
            with contextlib.suppress(asyncio.CancelledError):
                await goals_on
            with contextlib.suppress(asyncio.CancelledError):
                await sweep
            # the answers first, as «Стоп» ends them, while their tools, terminals and the database still work
            # (2026-11-11): their goals and agents go on after the restart; the goals' resuming is cancelled above,
            # so none of them is refused a turn and blocked
            await app.state.chat.stop()
            await app.state.mcp.stop()
            await app.state.keeper.stop()
            app.state.computer.close()
            with contextlib.suppress(asyncio.CancelledError):
                await catalog
            await app.state.status.stop()
            await app.state.watcher.stop()
            await app.state.processes.stop()
            await app.state.rescanner.stop()
            await app.state.ripgrep.stop()
            await app.state.programs.stop()
            await app.state.backup.stop()
            await app.state.scheduler.stop()
            await app.state.installer.stop()
            await app.state.gpu.stop()
            await app.state.visual.stop()
            await app.state.resources.stop()  # a failed fetch's next try (39.1)
            await app.state.setup.stop()
            await app.state.releases.stop()
            await app.state.scanner.stop()
            await app.state.tuner.stop()
            await app.state.telegram.stop()
            await app.state.claude_code.stop()
            await app.state.terminals.stop()
            await bus.publish("hub.stopping", {})
            db.close()
            log.info("hub stopped")

    # the interactive docs pages are for browsers, which this hub does not serve (D31)
    app = FastAPI(
        title="AFO hub", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(
        LocalOnlyMiddleware, allowed_hostnames=settings.allowed_hostnames, allowed_origins=settings.allowed_origins
    )
    # set by `python -m hub` once the server object exists; POST /v1/shutdown calls it
    app.state.request_shutdown = None
    app.include_router(system.router)
    app.include_router(language_api.router)
    app.include_router(events_ws.router)
    app.include_router(models.router)
    app.include_router(model_settings.router)
    app.include_router(portable.router)
    app.include_router(llama.router)
    app.include_router(install.router)
    app.include_router(visual.router)
    app.include_router(setup.router)
    app.include_router(release.router)
    app.include_router(tuning.router)
    app.include_router(gpu.router)
    app.include_router(support.router)
    app.include_router(memory.router)
    app.include_router(schedules.router)
    app.include_router(mcp_servers.router)
    app.include_router(mail.router)
    app.include_router(sandbox.router)
    app.include_router(conversations.router)
    app.include_router(goals.router)
    app.include_router(proving.router)
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
    app.include_router(history.router)
    app.include_router(agents.router)
    app.include_router(stats.router)
    app.include_router(advice.router)
    app.include_router(openai.router)
    # MCP for Cline, Claude Code and the like (D32): /mcp, with a client token
    mcp, mcp_app = mcp_server.build(app)
    app.router.routes.append(Route("/mcp", endpoint=mcp_app))
    return app


async def _sweep_sandboxes(app: FastAPI) -> None:
    """«Песочница» worktrees whose chat is gone - deleted while the hub was off, or before a sandbox went with its
    chat - removed with their branches and refs (D46). Git in a thread: the hub answers meanwhile."""

    def read(conn):
        return {r["id"] for r in conn.execute("SELECT id FROM conversations")}, {p.id: p for p in list_projects(conn)}

    try:
        live, by_id = await app.state.db.acall(read)
        gone = await asyncio.to_thread(sandbox_orphans, app.state.chat.sandboxes, live, by_id)
    except Exception:
        log.exception("left sandboxes were not swept")
        return
    if gone:
        log.info("sandboxes of chats that are gone, removed: %s", ", ".join(gone))


async def _resume_goals(app: FastAPI) -> None:
    """Chats' goals the last hub left at work go on (D44, D16) - after the first catalog sync."""
    with contextlib.suppress(Exception):
        await app.state.initial_sync
    try:
        resumed = await app.state.goals.start()
    except Exception:
        log.exception("chats' goals did not go on after the restart")
        return
    if resumed:
        log.info("chats' goals going on after a restart: %s", resumed)


async def _initial_sync(app: FastAPI) -> None:
    settings = app.state.settings
    try:  # AFO's own engine missing: fetched now, the wizard's Setup starts llama-swap when it is in (39.1)
        found = await app.state.resources.check()
        log.info("local models' programs: %s %s", found["status"], found.get("words", ""))
    except Exception:
        log.exception("looking at the local models' programs failed")
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
