"""Client tokens (D31): every client has its own token; the hub stores only its SHA-256 hash. A token starts with
`lct_` (D46, step 13.12): wherever one shows up - a command printing a token file, a config in a project - the secret
filter knows it; the tokens made before that are masked by their value (`load_token_files`)."""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .db import utc_now
from .secret_filter import mask_exactly

APP_CLIENT = "app"
TOKEN_PREFIX = "lct_"


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    cloud: bool  # D32: data it receives may go to a cloud model


@dataclass(frozen=True)
class ClientRecord:
    name: str
    cloud: bool
    created_at: str
    revoked_at: str | None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def load_token_files(tokens_dir: Path) -> int:
    """The tokens kept in data/tokens are masked by their value in all a model sees, an older one without the prefix
    too: a command in a trusted chat that prints one shows the mask (D46, step 13.12). How many there are."""
    found = 0
    for file in tokens_dir.glob("*.token"):
        try:
            mask_exactly(file.read_text(encoding="utf-8"))
        except OSError:
            continue
        found += 1
    return found


def create_client(conn: sqlite3.Connection, name: str, *, cloud: bool = True) -> str:
    """Registers a client and returns its token. The token is not stored, so show it now or lose it."""
    token = new_token()
    conn.execute(
        "INSERT INTO clients (id, name, token_hash, cloud) VALUES (?, ?, ?, ?)",
        (uuid.uuid4().hex, name, hash_token(token), int(cloud)),
    )
    return token


def rotate_token(conn: sqlite3.Connection, name: str) -> str:
    """A new token for an existing client; the old one stops working. Also un-revokes the client."""
    token = new_token()
    updated = conn.execute(
        "UPDATE clients SET token_hash = ?, revoked_at = NULL WHERE name = ?", (hash_token(token), name)
    ).rowcount
    if not updated:
        raise KeyError(name)
    return token


def revoke_client(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "UPDATE clients SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL", (utc_now(), name)
        ).rowcount
    )


def list_clients(conn: sqlite3.Connection) -> list[ClientRecord]:
    rows = conn.execute("SELECT name, cloud, created_at, revoked_at FROM clients ORDER BY created_at").fetchall()
    return [ClientRecord(r["name"], bool(r["cloud"]), r["created_at"], r["revoked_at"]) for r in rows]


def find_client(conn: sqlite3.Connection, token: str) -> Client | None:
    row = conn.execute(
        "SELECT id, name, cloud FROM clients WHERE token_hash = ? AND revoked_at IS NULL", (hash_token(token),)
    ).fetchone()
    return Client(row["id"], row["name"], bool(row["cloud"])) if row else None


def bearer_token(authorization: str | None) -> str | None:
    """The token from an `Authorization: Bearer <token>` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def ensure_app_token(conn: sqlite3.Connection, tokens_dir: Path) -> Path:
    """The desktop app's token lives in tokens/app.token, so the app can read it without asking.

    Creates the app client on first start, and gives it a new token when the file is missing or no
    longer matches. The app shows data to the user and is not a cloud client itself; which data may
    reach a cloud model is decided per model and per project (D10).
    """
    token_file = tokens_dir / f"{APP_CLIENT}.token"
    if token_file.is_file():
        stored = token_file.read_text(encoding="utf-8").strip()
        if stored and find_client(conn, stored):
            return token_file
    exists = conn.execute("SELECT 1 FROM clients WHERE name = ?", (APP_CLIENT,)).fetchone()
    token = rotate_token(conn, APP_CLIENT) if exists else create_client(conn, APP_CLIENT, cloud=False)
    tokens_dir.mkdir(parents=True, exist_ok=True)
    tmp = token_file.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    os.replace(tmp, token_file)
    return token_file
