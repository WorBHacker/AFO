"""The stdio adapter to the hub's MCP server (D32): `python -m hub mcp --client NAME`.

For MCP clients that can only start a process and talk to it over stdin / stdout. Every JSON-RPC message from the
client goes to the hub's /mcp as it is, with the client's token, and the hub's answer comes back on stdout - one
message per line. The hub's MCP is stateless with JSON answers, so the adapter keeps nothing but the protocol
version the hub agreed on. Requests run side by side: a slow tool does not hold up the others.

The token comes from data/tokens/<NAME>.token (`python -m hub token create NAME --save`) or from $LOCALCHAT_TOKEN,
so a client's config holds no secret. When no hub answers, the adapter starts one, as the app does (D2).
stdout carries only protocol messages; the adapter logs to data/logs/hub-mcp.log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from common.paths import DataPaths

from .locate import HubUnavailable, running_hub

log = logging.getLogger("hub-mcp")

TOKEN_ENV = "LOCALCHAT_TOKEN"
TIMEOUT_S = 300.0  # a tool may take a while (indexing a big project for find_symbol)
ACCEPT = "application/json, text/event-stream"
PARSE_ERROR = -32700
INTERNAL_ERROR = -32603
HUB_ERROR = -32000  # JSON-RPC's range for server errors: the hub is away, or refused the token


def client_token(paths: DataPaths, name: str) -> str | None:
    """The client's token: $LOCALCHAT_TOKEN, else the file `token create NAME --save` wrote."""
    if token := os.environ.get(TOKEN_ENV, "").strip():
        return token
    try:
        return (paths.tokens / f"{name}.token").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def error_reply(message_id: Any, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def sse_messages(text: str) -> list[dict]:
    """The JSON-RPC messages of an event stream (the hub answers with JSON, but a stream is valid MCP too)."""
    found, data = [], []
    for line in [*text.splitlines(), ""]:
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            found.append(json.loads("\n".join(data)))
            data = []
    return found


class Bridge:
    """Forwards one client message at a time to the hub's /mcp and returns what the client should get back."""

    def __init__(self, base_url: str, token: str, http: httpx.AsyncClient, reconnect: Callable[[], str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = http
        self.reconnect = reconnect  # finds (or starts) the hub again when it went away; returns its address
        self.protocol: str | None = None

    async def forward(self, line: bytes) -> list[dict]:
        try:
            message = json.loads(line)
        except ValueError:
            return [error_reply(None, PARSE_ERROR, "not a JSON-RPC message")]
        message_id = message.get("id") if isinstance(message, dict) else None
        is_request = isinstance(message, dict) and "method" in message and message_id is not None

        def failed(code: int, text: str) -> list[dict]:
            return [error_reply(message_id, code, text)] if is_request else []  # a notification gets no answer

        try:
            response = await self._post(line)
        except httpx.TransportError as exc:
            log.warning("the hub at %s did not answer: %s", self.base_url, exc)
            return failed(HUB_ERROR, f"AFO hub does not answer at {self.base_url}")
        if response.status_code == 401:
            return failed(HUB_ERROR, "the hub refused the token: python -m hub token create <name> --save")
        if response.status_code == 202 or not response.content:
            return []
        if response.status_code >= 400 and "json" not in response.headers.get("content-type", ""):
            return failed(HUB_ERROR, f"the hub answered {response.status_code}: {response.text[:300]}")
        try:
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                replies = sse_messages(response.text)
            else:
                body = response.json()
                replies = body if isinstance(body, list) else [body]
        except ValueError:
            return failed(INTERNAL_ERROR, "the hub's answer is not JSON")
        for reply in replies:
            version = (reply.get("result") or {}).get("protocolVersion") if isinstance(reply, dict) else None
            if version:
                self.protocol = version  # the version the hub agreed on, sent with every later request
        return replies

    async def _post(self, body: bytes) -> httpx.Response:
        try:
            return await self.http.post(f"{self.base_url}/mcp", content=body, headers=self._headers())
        except httpx.ConnectError:
            if self.reconnect is None:
                raise
            try:
                self.base_url = await asyncio.to_thread(self.reconnect)
            except HubUnavailable as exc:
                raise httpx.ConnectError(str(exc)) from exc
            return await self.http.post(f"{self.base_url}/mcp", content=body, headers=self._headers())

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": ACCEPT, "Content-Type": "application/json"}
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        return headers


async def pump(bridge: Bridge, read_line: Callable[[], Awaitable[bytes]], write: Callable[[bytes], None]) -> None:
    """Reads client messages until the input ends; each is forwarded on its own, answers are written whole."""
    pending: set[asyncio.Task] = set()

    async def handle(line: bytes) -> None:
        try:
            replies = await bridge.forward(line)
        except Exception:
            log.exception("forwarding failed")
            replies = [error_reply(None, INTERNAL_ERROR, "the adapter failed; see data/logs/hub-mcp.log")]
        for reply in replies:
            write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")

    while line := await read_line():
        if line.strip():
            task = asyncio.create_task(handle(line.strip()))
            pending.add(task)
            task.add_done_callback(pending.discard)
    if pending:
        await asyncio.gather(*pending)


async def run(paths: DataPaths, token: str) -> None:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer

    def write(data: bytes) -> None:
        stdout.write(data)
        stdout.flush()

    base = await asyncio.to_thread(running_hub, paths)
    log.info("forwarding stdio to %s/mcp", base)
    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=5.0)) as http:
        bridge = Bridge(base, token, http, reconnect=lambda: running_hub(paths))
        await pump(bridge, lambda: asyncio.to_thread(stdin.readline), write)
    log.info("the client closed stdin; the adapter exits")


def main(paths: DataPaths, client: str) -> int:
    token = client_token(paths, client)
    if not token:
        print(
            f"No token for the client {client!r}: create one with `python -m hub token create {client} --save` "
            f"(add --local for a client that works only with local models), or set ${TOKEN_ENV}",
            file=sys.stderr,
        )
        return 1
    try:
        asyncio.run(run(paths, token))
    except HubUnavailable as exc:
        print(f"AFO hub: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0
