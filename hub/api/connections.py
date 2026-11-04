"""/v1/connections - API keys, tokens and addresses (D13) behind the user's password. The desktop app only.

GET lists them: set or not, an address as it is, a key's last four characters while unlocked - never a key. A change
needs the password: POST /unlock gives a ticket the app sends as X-LocalChat-Unlock with each change; it lapses after
ten minutes without use, and POST /lock ends it at once. Changing a provider's key refreshes the model catalog, so
its models appear or disappear with it. Wrong passwords go to the event feed (connections.denied).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import connections as conns
from ..auth import APP_CLIENT, Client
from ..telegram import LINK_TTL_S, linked_chat, new_link_code, unlink
from .deps import require_client

router = APIRouter(prefix="/v1/connections")

Ticket = Annotated[str | None, Header(alias=conns.UNLOCK_HEADER)]


class NewPassword(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    old: str = Field(default="", max_length=200)


class Password(BaseModel):
    password: str = Field(max_length=200)


class Value(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


class Chosen(BaseModel):
    ids: list[str] = Field(max_length=500)


def _app_only(client: Client) -> None:
    if client.name != APP_CLIENT:
        raise HTTPException(status_code=403, detail="only the desktop app manages connections")


def _refused(exc: conns.LockError) -> HTTPException:
    detail = {"message": str(exc), "code": exc.code, "retry_after": exc.retry_after}
    return HTTPException(status_code=exc.status, detail=detail)


async def _open(request: Request, client: Client, ticket: str | None) -> None:
    """The app, unlocked: a change may go through."""
    _app_only(client)
    try:
        request.app.state.lock.check(ticket)
    except conns.LockError as exc:
        raise _refused(exc) from exc


def _connection(name: str) -> conns.Connection:
    try:
        return conns.known(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _denied(request: Request, exc: conns.LockError) -> None:
    if exc.code in ("wrong_password", "wait"):
        await request.app.state.bus.publish("connections.denied", {"wait": exc.retry_after})


@router.get("")
async def list_connections(request: Request, client: Annotated[Client, Depends(require_client)],
                           ticket: Ticket = None) -> dict:  # fmt: skip
    _app_only(client)
    state = request.app.state
    unlocked = state.lock.is_open(ticket)
    found, chat = await state.db.acall(
        lambda conn: (conns.rows(state.secrets, conn, unlocked=unlocked), linked_chat(conn))
    )
    return {"password": state.lock.has_password, "unlocked": unlocked, "connections": found,
            "telegram": {"linked": chat is not None}}  # fmt: skip


@router.post("/password")
async def set_password(body: NewPassword, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """The first password, or a new one (with the old). Every open window is locked again."""
    _app_only(client)
    lock = request.app.state.lock
    had = lock.has_password
    try:
        await asyncio.to_thread(lock.set_password, body.password, body.old)
    except conns.LockError as exc:
        await _denied(request, exc)
        raise _refused(exc) from exc
    await request.app.state.bus.publish("connections.password", {"changed": had})
    return {"password": True}


@router.post("/unlock")
async def unlock(body: Password, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    _app_only(client)
    try:
        ticket = await asyncio.to_thread(request.app.state.lock.unlock, body.password)
    except conns.LockError as exc:
        await _denied(request, exc)
        raise _refused(exc) from exc
    return {"ticket": ticket, "valid_s": conns.TICKET_S}


@router.post("/lock")
async def lock(request: Request, client: Annotated[Client, Depends(require_client)], ticket: Ticket = None) -> dict:
    _app_only(client)
    request.app.state.lock.lock(ticket)
    return {"unlocked": False}


@router.post("/telegram/link")
async def telegram_link(request: Request, client: Annotated[Client, Depends(require_client)],
                        ticket: Ticket = None) -> dict:  # fmt: skip
    """A one-time code for "/link <code>" to the user's bot: the chat that sends it gets the approvals (D21)."""
    await _open(request, client, ticket)
    code = await request.app.state.db.acall(new_link_code)
    return {"code": code, "valid_s": LINK_TTL_S}


@router.delete("/telegram/link")
async def telegram_unlink(request: Request, client: Annotated[Client, Depends(require_client)],
                          ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    await request.app.state.db.acall(unlink)
    await request.app.state.bus.publish("telegram.unlinked", {})
    return {"linked": False}


@router.put("/{name}")
async def set_connection(name: str, body: Value, request: Request, client: Annotated[Client, Depends(require_client)],
                         ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    connection = _connection(name)
    try:
        value = conns.clean_value(connection, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state = request.app.state
    if connection.kind == "address":
        await state.db.acall(lambda conn: conns.set_address(conn, name, value))
    else:
        state.secrets.set(name, value)
    return await _changed(request, connection, is_set=True)


@router.delete("/{name}")
async def delete_connection(name: str, request: Request, client: Annotated[Client, Depends(require_client)],
                            ticket: Ticket = None) -> dict:  # fmt: skip
    await _open(request, client, ticket)
    connection = _connection(name)
    state = request.app.state
    if connection.kind == "address":
        await state.db.acall(lambda conn: conns.set_address(conn, name, None))
    else:
        state.secrets.delete(name)
    return await _changed(request, connection, is_set=False)


@router.get("/{name}/models")
async def provider_models(name: str, request: Request, client: Annotated[Client, Depends(require_client)]) -> dict:
    """Everything a provider with too many models (OpenRouter) has, and which ones the user chose to offer."""
    from ..gateway.base import ProviderError

    _app_only(client)
    connection = _chooser(name)
    state = request.app.state
    provider = state.gateway.provider(connection.provider)
    if provider is None:
        raise HTTPException(status_code=404, detail="this provider is not in the hub")
    try:
        everything = await provider.everything()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    chosen = set(await state.db.acall(lambda conn: conns.chosen_models(conn, connection.provider)))
    return {"models": [{**m, "chosen": m["id"] in chosen} for m in everything], "chosen": sorted(chosen)}


@router.put("/{name}/models")
async def choose_models(name: str, body: Chosen, request: Request, client: Annotated[Client, Depends(require_client)],
                        ticket: Ticket = None) -> dict:  # fmt: skip
    """The models to offer; the catalog follows at once."""
    await _open(request, client, ticket)
    connection = _chooser(name)
    state = request.app.state
    ids = await state.db.acall(lambda conn: conns.set_chosen_models(conn, connection.provider, body.ids))
    catalog = await state.gateway.sync_catalog()
    await state.bus.publish("models.synced", catalog)
    return {"chosen": ids, "catalog": catalog}


def _chooser(name: str) -> conns.Connection:
    connection = _connection(name)
    if not connection.choose:
        raise HTTPException(status_code=404, detail=f"{connection.title}: its models are not chosen by hand")
    return connection


@router.post("/{name}/check")
async def check_connection(name: str, request: Request, client: Annotated[Client, Depends(require_client)],
                           ticket: Ticket = None) -> dict:  # fmt: skip
    """Tries the connection the way the hub uses it: lists the provider's models, searches, asks the bot its name."""
    await _open(request, client, ticket)
    connection = _connection(name)
    ok, detail = await _try(request, connection)
    return {"name": name, "ok": ok, "detail": detail}


async def _changed(request: Request, connection: conns.Connection, *, is_set: bool) -> dict[str, Any]:
    state = request.app.state
    await state.bus.publish("secrets.changed", {"name": connection.name, "title": connection.title, "set": is_set})
    result: dict[str, Any] = {"name": connection.name, "set": is_set}
    if connection.provider:
        result["catalog"] = await state.gateway.sync_catalog()
        await state.bus.publish("models.synced", result["catalog"])
    return result


async def _try(request: Request, connection: conns.Connection) -> tuple[bool, str]:
    from .. import websearch
    from ..gateway.base import ProviderError

    state = request.app.state
    if connection.kind == "address":
        is_set = bool(await state.db.acall(lambda conn: conns.address(conn, connection.name)))
    else:
        is_set = bool(state.secrets.get(connection.name))
    if not is_set:
        return False, "не задан: проверять нечего"
    if connection.provider:
        provider = state.gateway.provider(connection.provider)
        if provider is None:
            return False, "адаптера этого поставщика в хабе нет"
        try:
            models = await provider.list_models()
        except ProviderError as exc:
            return False, exc.detail
        return bool(models), f"ключ работает: моделей — {len(models)}" if models else "ключ принят, но моделей нет"
    engines = {conns.BRAVE_SEARCH_KEY: "brave", conns.TAVILY_API_KEY: "tavily", conns.SEARXNG_URL: "searxng"}
    if connection.name in engines:
        engine = engines[connection.name]
        try:
            found = await websearch.search("AFO", engine=engine, store=state.secrets, db=state.db, count=3)
        except websearch.SearchError as exc:
            return False, str(exc)
        return True, f"поиск работает: результатов — {len(found)}"
    if connection.name == conns.HUGGINGFACE_TOKEN:
        from .. import hf

        try:
            who = await hf.whoami(state.secrets.get(connection.name))
        except hf.HubError as exc:
            return False, str(exc)
        return True, f"токен принят: {who}"
    if connection.name == conns.TELEGRAM_TOKEN:
        try:
            me = await state.telegram.me()
        except LookupError:
            return False, "токен не задан"
        except Exception as exc:  # the Bot API's refusal or the network's trouble, said briefly
            return False, f"Telegram: {exc}"
        return True, f"бот @{me.get('username', '?')} на связи"
    return False, "проверки для этого подключения нет"
