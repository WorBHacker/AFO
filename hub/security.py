"""Only this computer's own programs may talk to the hub (D31).

A request whose Host is not this computer is refused: that is what a DNS-rebinding page sends. A
request carrying an Origin header is refused too: browsers add it to every cross-site request and
every WebSocket handshake, while the hub's clients (the app, Cline, Claude Code) send none.
"""

from __future__ import annotations

import logging

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

log = logging.getLogger("hub.security")


def hostname_of(host_header: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '::1', 'LocalHost' -> 'localhost'."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, allowed_hostnames: frozenset[str], allowed_origins: frozenset[str]):
        self.app = app
        self.allowed_hostnames = allowed_hostnames
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        reason = self.refusal(Headers(scope=scope))
        if reason is None:
            await self.app(scope, receive, send)
            return
        log.warning("refused %s %s: %s", scope["type"], scope.get("path", ""), reason)
        if scope["type"] == "http":
            await JSONResponse({"error": "forbidden", "detail": reason}, status_code=403)(scope, receive, send)
        else:
            await WebSocketClose(code=1008)(scope, receive, send)

    def refusal(self, headers: Headers) -> str | None:
        host = headers.get("host", "")
        if hostname_of(host) not in self.allowed_hostnames:
            return f"host {host!r} is not this computer"
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return f"requests from web pages are not accepted (origin {origin!r})"
        return None
