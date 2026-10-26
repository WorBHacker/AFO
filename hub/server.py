"""The hub's web application: database, event bus and API in one process (D28)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import __version__

from .api import budgets, conversations, events_ws, models, projects, secrets, system
from .auth import ensure_app_token
from .chat import ChatService
from .db import Database, migrate
from .events import EventBus
from .gateway.registry import build_default_gateway
from .projects import list_projects
from .secrets import SecretStore, default_secret_store
from .security import LocalOnlyMiddleware
from .settings import HubSettings
from .system_status import StatusMonitor
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
        app.state.db = db
        app.state.bus = bus
        app.state.gateway = gateway
        app.state.chat = ChatService(db, bus, gateway)
        app.state.status = StatusMonitor(bus, settings.llama_swap_url, settings.status_interval_s)
        app.state.started_monotonic = time.monotonic()
        # the hub's own data may sit inside a project (this repository): its writes are not project changes
        app.state.watcher = ProjectWatcher(bus, ignore=[paths.root])
        await bus.publish("hub.started", {"version": __version__, "migrations_applied": applied})
        # the catalog syncs in the background: the hub answers at once even if llama-swap or the network is slow
        catalog = app.state.initial_sync = asyncio.create_task(_initial_sync(app))
        app.state.status.start()
        app.state.watcher.start(db.call(list_projects))
        log.info("hub ready, app token in %s", token_file)
        try:
            yield
        finally:
            catalog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await catalog
            await app.state.status.stop()
            await app.state.watcher.stop()
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
    app.include_router(conversations.router)
    app.include_router(secrets.router)
    app.include_router(budgets.router)
    app.include_router(projects.router)
    return app


async def _initial_sync(app: FastAPI) -> None:
    try:
        result = await app.state.gateway.sync_catalog()  # best effort: llama-swap or the network may be down
    except Exception:
        log.exception("startup catalog sync failed")
        return
    log.info("model catalog: %s", result)
    await app.state.bus.publish("models.synced", result)
