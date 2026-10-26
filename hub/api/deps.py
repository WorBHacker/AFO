"""Shared request dependencies: who the client is."""

from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket

from ..auth import Client, bearer_token, find_client


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
    return client
