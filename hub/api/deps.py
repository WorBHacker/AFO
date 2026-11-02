"""Shared request dependencies: who the client is, and what a client other than the window may ask for.

The window may do everything. Another client (Cline, Claude Code, VS Code, a script with its own token) reaches the
hub through MCP (/mcp, its own rules: D32), the OpenAI-compatible API and /v1/editor; over REST it may also read the
projects - as MCP lets it - and begin, read and continue conversations of its own. Everything else is the window's
(D31: rights by client; the audit found any token could schedule an agent's task or answer in any chat, D46).
"""

from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket

from ..auth import APP_CLIENT, Client, bearer_token, find_client

# (method, route) a client other than the window may use; the routes check the rest (a project's cloud flag, a
# secret file, a conversation's owner)
OUTSIDE_ROUTES = frozenset({
    ("GET", "/v1/info"), ("GET", "/v1/status"), ("GET", "/v1/models"), ("GET", "/v1/memory/facts"),
    ("GET", "/v1/editor"), ("POST", "/v1/editor/focus"), ("POST", "/v1/editor/command"),
    ("GET", "/v1/openai/models"), ("POST", "/v1/openai/chat/completions"),
    ("GET", "/v1/projects"),
    *(("GET", f"/v1/projects/{{project_id}}/{part}") for part in ("tree", "file", "git", "search", "files", "symbols",
                                                                   "diff")),
    ("GET", "/v1/conversations"), ("POST", "/v1/conversations"),
    *((method, "/v1/conversations/{conversation_id}") for method in ("GET", "PATCH", "DELETE")),
    ("POST", "/v1/conversations/{conversation_id}/messages"), ("POST", "/v1/conversations/{conversation_id}/cancel"),
    ("GET", "/v1/conversations/{conversation_id}/goal"),
})  # fmt: skip


async def client_from_headers(conn_holder: Request | WebSocket) -> Client | None:
    token = bearer_token(conn_holder.headers.get("authorization"))
    if token is None:
        return None
    db = conn_holder.app.state.db
    return await db.acall(lambda conn: find_client(conn, token))


async def require_client(request: Request) -> Client:
    client = await client_from_headers(request)
    if client is None:
        raise HTTPException(status_code=401, detail="a valid client token is required (Authorization: Bearer)")
    route = request.scope.get("route")
    if client.name != APP_CLIENT and (request.method, getattr(route, "path", "")) not in OUTSIDE_ROUTES:
        raise HTTPException(status_code=403, detail="only the desktop app may do this; other clients reach the hub "
                                                    "through MCP (/mcp), /v1/openai and /v1/editor")  # fmt: skip
    return client


async def own_conversation(request: Request, client: Client, conversation_id: str) -> None:
    """A client other than the window reaches only a conversation it began: another's is not there for it (404)."""
    if client.name == APP_CLIENT:
        return
    from .. import store

    owner = await request.app.state.db.acall(lambda conn: store.conversation_owner(conn, conversation_id))
    if owner != client.id:
        raise HTTPException(status_code=404, detail="no such conversation")
