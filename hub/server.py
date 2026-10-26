"""The hub's web application: database, event bus and API in one process (D28)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import __version__

from .api import events_ws, system
from .auth import ensure_app_token
from .db import Database, migrate
from .events import EventBus
from .security import LocalOnlyMiddleware
from .settings import HubSettings

log = logging.getLogger("hub")


def create_app(settings: HubSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        paths = settings.paths.ensure()
        db = Database(paths.db)
        applied = db.call(migrate)
        if applied:
            log.info("database %s: applied migrations %s", paths.db, applied)
        token_file = db.call(lambda conn: ensure_app_token(conn, paths.tokens))
        bus = EventBus(db)
        app.state.settings = settings
        app.state.db = db
        app.state.bus = bus
        app.state.started_monotonic = time.monotonic()
        await bus.publish("hub.started", {"version": __version__, "migrations_applied": applied})
        log.info("hub ready, app token in %s", token_file)
        try:
            yield
        finally:
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
    return app
