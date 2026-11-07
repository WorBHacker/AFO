"""Connections (D13): the API keys, tokens and addresses the hub uses, behind the user's password.

A key or a token lives in the Windows Credential Manager (hub/secrets.py) and never comes back out of the hub: the
window sees whether it is set and, while unlocked, its last four characters. An address (a SearXNG server) is not a
secret and lives in the settings table.

The password guards changes: setting, replacing or removing a connection, and linking the Telegram chat. The window
unlocks with it for a while (TICKET_S since the last use); after FREE_TRIES wrong passwords in a row the hub makes
the next try wait, longer each time. The hub itself uses the keys without the password - agents and the Telegram bot
work unattended - so the password keeps the keys from the window and the hub's API, not from Windows: the user's
Windows account reads its Credential Manager anyway. A forgotten password: `python -m hub connections reset`
removes it together with every saved connection.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from common.i18n import N_, tr

from .db import utc_now
from .secrets import ANTHROPIC_API_KEY, SecretStore

PASSWORD = "connections_password"  # in the secret store: the password's scrypt hash, its salt and cost
MIN_PASSWORD = 6
SCRYPT_N = 2**15  # 32 MiB and some 0.1 s per try: cheap for the user, slow for guessing
TICKET_S = 600  # an unlocked window stays unlocked this long after its last change
FREE_TRIES = 5
WAIT_S = (30, 60, 120, 300, 900)  # after the free tries, each wrong password makes the next one wait this long
ADDRESS_PREFIX = "connections."  # an address's key in the settings table
UNLOCK_HEADER = "X-LocalChat-Unlock"

GEMINI_API_KEY = "gemini_api_key"
VERTEX_ACCOUNT = "vertex_service_account"  # a Google service account's key file, kept as service_account() makes it
ACCOUNT_FIELDS = ("project_id", "client_email", "private_key_id", "private_key")
OPENAI_API_KEY = "openai_api_key"
DEEPSEEK_API_KEY = "deepseek_api_key"
OPENROUTER_API_KEY = "openrouter_api_key"
CHOSEN_PREFIX = "models."  # a provider's chosen models (OpenRouter's hundreds) in the settings table
BRAVE_SEARCH_KEY = "brave_search_api_key"
TAVILY_API_KEY = "tavily_api_key"
SEARXNG_URL = "searxng_url"
TELEGRAM_TOKEN = "telegram_bot_token"
HUGGINGFACE_TOKEN = "huggingface_token"  # hub/hf.py reads it (10.1)


@dataclass(frozen=True)
class Connection:
    name: str
    title: str  # N_(): tr() where it is shown
    kind: str  # "key" (an API key), "token" (a bot's token), "file" (a key file), "address" (not a secret: settings)
    purpose: str  # what it gives, for the window - N_(), as the title
    site: str  # where to get it
    hint: str  # what it looks like
    provider: str = ""  # the gateway provider whose models it brings: the catalog syncs when it changes
    choose: bool = False  # the provider has too many models to list: the user picks which ones the hub offers


CONNECTIONS: list[Connection] = [
    Connection(ANTHROPIC_API_KEY, "Anthropic", "key", N_("Модели Claude по ключу — оплата за токены"),
               "https://console.anthropic.com/settings/keys", "sk-ant-…", provider="anthropic"),
    Connection(OPENAI_API_KEY, "OpenAI", "key", N_("Модели GPT по ключу — оплата за токены"),
               "https://platform.openai.com/api-keys", "sk-…", provider="openai"),
    Connection(GEMINI_API_KEY, "Google Gemini", "key", N_("Модели Gemini. На бесплатном уровне Google может "
               "использовать запросы для улучшения своих моделей — для закрытого кода нужен платный"),
               "https://aistudio.google.com/apikey", "AIza…", provider="gemini"),
    Connection(VERTEX_ACCOUNT, "Google Vertex AI", "file", N_("Модели Gemini через Google Cloud — оплата со счёта "
               "Google Cloud, запросы не идут на обучение. Нужен файл ключа сервисного аккаунта (JSON) с ролью "
               "«Vertex AI User»"), "https://console.cloud.google.com/iam-admin/serviceaccounts", "….json",
               provider="vertex"),
    Connection(DEEPSEEK_API_KEY, "DeepSeek", "key", N_("Модели DeepSeek — недорогие; запросы обрабатываются на "
               "серверах в Китае — для закрытого кода решайте сами"), "https://platform.deepseek.com/api_keys",
               "sk-…", provider="deepseek"),
    Connection(OPENROUTER_API_KEY, "OpenRouter", "key", N_("Сотни моделей разных компаний по одному ключу; какие "
               "показывать в списке — кнопка «Модели…»"), "https://openrouter.ai/settings/keys", "sk-or-…",
               provider="openrouter", choose=True),
    Connection(SEARXNG_URL, "SearXNG", "address", N_("Свой поисковик для web_search — бесплатно и без лимитов; "
               "адрес сервера (ищется первым)"), "https://docs.searxng.org/admin/installation-docker.html",
               "http://127.0.0.1:8888"),
    Connection(TAVILY_API_KEY, "Tavily", "key", N_("Поиск в сети для моделей (web_search): 1000 поисков в месяц "
               "бесплатно, без карты"), "https://app.tavily.com/home", "tvly-…"),
    Connection(BRAVE_SEARCH_KEY, "Brave Search", "key", N_("Поиск в сети для моделей (web_search): $5 за 1000 "
               "поисков, $5 в месяц в подарок, нужна карта (ищется последним)"),
               "https://api-dashboard.search.brave.com/app/keys", "BSA…"),
    Connection(TELEGRAM_TOKEN, N_("Telegram-бот"), "token", N_("Подтверждения с телефона: токен вашего бота"),
               "https://t.me/BotFather", "123456789:AA…"),
    Connection(HUGGINGFACE_TOKEN, "Hugging Face", "token", N_("Установка закрытых моделей (Llama, часть Gemma): "
               "токен для чтения. Лицензию модели примите сами на её странице; открытые модели ставятся и без него"),
               "https://huggingface.co/settings/tokens", "hf_…"),
]  # fmt: skip
BY_NAME = {c.name: c for c in CONNECTIONS}
TELEGRAM_FORMAT = re.compile(r"\d{5,}:[\w-]{20,}")


class LockError(Exception):
    """A refused password or a locked connection; `code` for the app, `status` for HTTP."""

    def __init__(self, message: str, code: str, status: int = 403, retry_after: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


# --- the password ---


def hash_password(password: str, *, salt: bytes | None = None, n: int | None = None) -> dict[str, Any]:
    salt = salt if salt is not None else os.urandom(16)
    n = n or SCRYPT_N
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=8, p=1, maxmem=128 * 1024 * 1024, dklen=32)
    return {"salt": salt.hex(), "hash": digest.hex(), "n": n}


def password_matches(record: dict[str, Any], password: str) -> bool:
    again = hash_password(password, salt=bytes.fromhex(record["salt"]), n=int(record["n"]))
    return hmac.compare_digest(again["hash"], record["hash"])


class ConnectionLock:
    """The password and the window's unlocks. Tickets and the count of wrong tries live in the hub's memory."""

    def __init__(self, store: SecretStore, clock: Callable[[], float] = time.monotonic):
        self._store = store
        self._clock = clock
        self._tickets: dict[str, float] = {}  # ticket -> its last use
        self._failures = 0
        self._wait_until = 0.0

    def _record(self) -> dict[str, Any] | None:
        raw = self._store.get(PASSWORD)
        try:
            record = json.loads(raw) if raw else None
        except ValueError:
            return None
        return record if isinstance(record, dict) and {"salt", "hash", "n"} <= set(record) else None

    @property
    def has_password(self) -> bool:
        return self._record() is not None

    def set_password(self, new: str, old: str = "") -> None:
        """The first password, or a new one in place of `old`. Every unlock ends: the window asks again."""
        if len(new) < MIN_PASSWORD:
            raise LockError(tr("пароль — не короче {count} символов", count=MIN_PASSWORD), "weak", 422)
        if self.has_password:
            self._verify(old)
        self._store.set(PASSWORD, json.dumps(hash_password(new)))
        self._tickets.clear()

    def _verify(self, password: str) -> None:
        now = self._clock()
        if now < self._wait_until:
            wait = math.ceil(self._wait_until - now)
            said = tr("слишком много неверных паролей: следующая попытка через {wait} с", wait=wait)
            raise LockError(said, "wait", 429, wait)
        record = self._record()
        if record is None:
            raise LockError(tr("пароль подключений ещё не задан"), "no_password", 409)
        if not password_matches(record, password):
            self._failures += 1
            if self._failures >= FREE_TRIES:
                self._wait_until = now + WAIT_S[min(self._failures - FREE_TRIES, len(WAIT_S) - 1)]
            raise LockError(tr("неверный пароль"), "wrong_password", 403)
        self._failures = 0

    def unlock(self, password: str) -> str:
        self._verify(password)
        ticket = secrets.token_urlsafe(24)
        self._tickets[ticket] = self._clock()
        return ticket

    def is_open(self, ticket: str | None) -> bool:
        now = self._clock()
        self._tickets = {t: at for t, at in self._tickets.items() if now - at < TICKET_S}
        return bool(ticket) and ticket in self._tickets and self.has_password

    def check(self, ticket: str | None) -> None:
        """Lets a change through with a live ticket (and keeps the ticket alive); refuses otherwise."""
        if not self.is_open(ticket):
            raise LockError(tr("подключения закрыты: введите пароль"), "locked", 401)
        self._tickets[str(ticket)] = self._clock()

    def lock(self, ticket: str | None = None) -> None:
        if ticket is None:
            self._tickets.clear()
        else:
            self._tickets.pop(ticket, None)


def reset(store: SecretStore, conn: sqlite3.Connection) -> list[str]:
    """A forgotten password: it goes, and so does every saved connection - nothing set under it stays behind."""
    names = kept(conn)
    removed = [name for name in names if name in BY_NAME and store.delete(name)]
    removed += [c.name for c in CONNECTIONS if c.kind == "address" and set_address(conn, c.name, None)]
    removed += [name for name in names if name not in BY_NAME and store.delete(name)]
    store.delete(PASSWORD)
    return removed


def kept(conn: sqlite3.Connection) -> list[str]:
    """The names of every secret the hub may keep under the password: the connections' keys and tokens, the MCP
    servers' environments (9.5), the mailbox's app password and the agent's API keys (9.4b)."""
    names = [c.name for c in CONNECTIONS if c.kind != "address"]
    names += [f"mcp_env.{r['name']}" for r in conn.execute("SELECT name FROM mcp_servers ORDER BY name")]
    names.append("mail_password")
    keys = conn.execute("SELECT value FROM settings WHERE key = 'agent.keys'").fetchone()
    names += [f"agent_key.{key.get('name')}" for key in (json.loads(keys["value"]) if keys else [])]
    return names


# --- the connections ---


def known(name: str) -> Connection:
    found = BY_NAME.get(name)
    if found is None:
        raise KeyError(f"unknown connection {name!r}; known: {', '.join(BY_NAME)}")
    return found


def address(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (ADDRESS_PREFIX + name,)).fetchone()
    return json.loads(row["value"]) if row else ""


def set_address(conn: sqlite3.Connection, name: str, value: str | None) -> bool:
    """Saves an address, or removes it with None; says whether something changed."""
    if value is None:
        return conn.execute("DELETE FROM settings WHERE key = ?", (ADDRESS_PREFIX + name,)).rowcount > 0
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (ADDRESS_PREFIX + name, json.dumps(value), utc_now()),
    )
    return True


def chosen_models(conn: sqlite3.Connection, provider: str) -> list[str]:
    """The models of a provider the user chose to offer (OpenRouter); [] - none chosen yet."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (CHOSEN_PREFIX + provider,)).fetchone()
    return list(json.loads(row["value"])) if row else []


def set_chosen_models(conn: sqlite3.Connection, provider: str, ids: list[str]) -> list[str]:
    ids = sorted({i.strip() for i in ids if i.strip()})
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET"
        " value = excluded.value, updated_at = excluded.updated_at",
        (CHOSEN_PREFIX + provider, json.dumps(ids), utc_now()),
    )
    return ids


def service_account(text: str) -> str:
    """A Google service account's key file as it is kept (stage 30): only what signs in - the project, the account,
    its key and the key's id; ValueError with what is wrong, in the window's words."""
    try:
        found = json.loads(text)
    except ValueError:
        found = None
    if not isinstance(found, dict) or found.get("type") != "service_account":
        raise ValueError(tr('это не файл ключа сервисного аккаунта Google (JSON с "type": "service_account"): '
                            "Google Cloud → IAM → Сервисные аккаунты → Ключи → Добавить ключ → JSON"))  # fmt: skip
    if not all(isinstance(found.get(name), str) and found[name] for name in ACCOUNT_FIELDS):
        raise ValueError(tr("в файле ключа нет проекта, аккаунта или самого ключа — скачайте ключ заново"))
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        load_pem_private_key(found["private_key"].encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError(tr("ключ в файле не читается — скачайте ключ заново")) from exc
    return json.dumps({name: found[name] for name in ACCOUNT_FIELDS}, separators=(",", ":"))


def clean_value(connection: Connection, value: str) -> str:
    """The value as it is stored, or ValueError with what is wrong (in the window's words)."""
    value = value.strip()
    if connection.kind == "file":
        return service_account(value)
    if connection.kind == "address":
        value = value.rstrip("/")
        if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", value):
            raise ValueError(tr("адрес начинается с http:// или https://, например http://127.0.0.1:8888"))
        return value
    if len(value) < 8 or any(ch.isspace() for ch in value):
        raise ValueError(tr("это не похоже на ключ: слишком коротко или с пробелами внутри"))
    if connection.name == TELEGRAM_TOKEN and not TELEGRAM_FORMAT.fullmatch(value):
        raise ValueError(tr("токен бота выглядит как 123456789:AA… — его выдаёт @BotFather"))
    return value


def rows(store: SecretStore, conn: sqlite3.Connection, *, unlocked: bool) -> list[dict[str, Any]]:
    """What the window shows: never a key's value; its last four characters only while unlocked."""
    out = []
    for c in CONNECTIONS:
        row: dict[str, Any] = {"name": c.name, "title": tr(c.title), "kind": c.kind, "purpose": tr(c.purpose),
                               "site": c.site, "hint": c.hint, "choose": c.choose}  # fmt: skip
        if c.choose:
            row["chosen"] = len(chosen_models(conn, c.provider))
        if c.kind == "address":
            value = address(conn, c.name)
            row["value"] = value
        else:
            value = store.get(c.name) or ""
            if c.kind == "file":  # the key's id, as the console lists the account's keys
                row["tail"] = _key_id(value)[-4:] if unlocked else ""
            else:
                row["tail"] = value[-4:] if unlocked and len(value) >= 12 else ""
        row["set"] = bool(value)
        out.append(row)
    return out


def _key_id(kept: str) -> str:
    try:
        found = json.loads(kept) if kept else {}
    except ValueError:
        return ""
    return str(found.get("private_key_id") or "") if isinstance(found, dict) else ""
