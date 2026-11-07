"""API keys and other secrets (D13). On Windows keyring stores them in the Credential Manager.

Secrets never go into settings.json or the database, and their values are never logged or returned by
the API - the API reports only whether a key is set. Which secrets the hub knows, and the password that guards
them, is hub/connections.py. What the hub keeps is masked by its value in all a model sees and in the logs
(`Masking`, 22.1).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterable
from typing import Protocol

from .secret_filter import mask_exactly

log = logging.getLogger("hub.secrets")

SERVICE = "AFO"
OLD_SERVICE = "LocalChat"  # the name before 10.4b: a secret still kept under it moves to SERVICE when first read
# "memory" keeps secrets in the process only: tests and throwaway runs never touch the OS store
STORE_ENV = "LOCALCHAT_SECRETS"

ANTHROPIC_API_KEY = "anthropic_api_key"
MASK_FROM = 8  # a kept value this long or longer is masked by itself: the window takes no shorter key (22.1)
SECRET_NAME = re.compile(r"secret|token|passw|pwd|key|credential|auth", re.I)  # in a server's environment


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...


ENTRY_BYTES = 2560  # one Credential Manager entry: keyring writes UTF-16, so 1280 characters (CredWrite: error 1783)
PART = 600  # characters in one part of a longer value: under ENTRY_BYTES even if every one takes two UTF-16 units
PARTS = "afo-parts:"  # the entry of a value kept in parts says how many; they follow as "<name>#1", "<name>#2"…


class KeyringSecretStore:
    """Backed by the OS secret store (Windows Credential Manager via keyring's WinVaultKeyring). A value longer than
    one entry takes - a Google service account's key file (stage 30), a long MCP server environment - is kept in
    parts (step 30.1)."""

    def __init__(self, service: str = SERVICE, old: str | None = OLD_SERVICE):
        self.service = service
        self.old = old

    def get(self, name: str) -> str | None:
        import keyring

        value = self._read(self.service, name)
        if value is None and self.old:
            value = keyring.get_password(self.old, name)
            if value is not None:  # kept under the old name (never in parts): moved
                self.set(name, value)
                self._drop(self.old, name)
        return value

    def set(self, name: str, value: str) -> None:
        import keyring

        before = self._parts(name)
        if len(value.encode("utf-16-le")) <= ENTRY_BYTES and not value.startswith(PARTS):
            keyring.set_password(self.service, name, value)
            count = 0
        else:
            pieces = [value[i : i + PART] for i in range(0, len(value), PART)]
            for number, piece in enumerate(pieces, 1):
                keyring.set_password(self.service, f"{name}#{number}", piece)
            keyring.set_password(self.service, name, f"{PARTS}{len(pieces)}")
            count = len(pieces)
        for number in range(count + 1, before + 1):  # a shorter value than before: its extra parts go
            self._drop(self.service, f"{name}#{number}")

    def delete(self, name: str) -> bool:
        """Under the old name too, so a secret never comes back from there."""
        for number in range(1, self._parts(name) + 1):
            self._drop(self.service, f"{name}#{number}")
        gone = self._drop(self.service, name)
        return (self._drop(self.old, name) if self.old else False) or gone

    def _read(self, service: str, name: str) -> str | None:
        import keyring

        value = keyring.get_password(service, name)
        if value is None or not value.startswith(PARTS):
            return value
        pieces = [keyring.get_password(service, f"{name}#{n}") for n in range(1, int(value[len(PARTS) :]) + 1)]
        if None in pieces:
            log.warning("secret %s: a part of it is missing from the Credential Manager", name)
            return None
        return "".join(str(piece) for piece in pieces)

    def _parts(self, name: str) -> int:
        import keyring

        value = keyring.get_password(self.service, name) or ""
        return int(value[len(PARTS) :]) if value.startswith(PARTS) else 0

    @staticmethod
    def _drop(service: str, name: str) -> bool:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(service, name)
            return True
        except PasswordDeleteError:
            return False


class MemorySecretStore:
    """In-process secrets, for tests and for a run that must not touch the OS store."""

    def __init__(self, initial: dict[str, str] | None = None):
        self._data = dict(initial or {})

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value

    def delete(self, name: str) -> bool:
        return self._data.pop(name, None) is not None


class Masking:
    """A store whose every value - kept at the hub's start, read or set later - is masked by itself in all a model
    sees and in the logs (step 22.1): a key, a password or a token whose look no pattern of the secret filter knows
    does not reach either as it is. A replaced or removed value stays masked until the hub restarts: it may still
    work where it came from."""

    def __init__(self, store: SecretStore):
        self.store = store

    def get(self, name: str) -> str | None:
        value = self.store.get(name)
        if value:
            masked(value)
        return value

    def set(self, name: str, value: str) -> None:
        masked(value)
        self.store.set(name, value)

    def delete(self, name: str) -> bool:
        return self.store.delete(name)

    def load(self, names: Iterable[str]) -> None:
        """At the start: what is kept already is known before a model or a log could show it."""
        for name in names:
            self.get(name)


def masked(value: str) -> None:
    """A kept value into the secret filter. An MCP server's environment (JSON) gives the values whose names say they
    are secrets - not its LOG_LEVEL; an app password as Google shows it (abcd efgh ijkl mnop) counts without its
    spaces too."""
    if value.lstrip().startswith("{"):
        try:
            env = json.loads(value)
        except ValueError:
            env = None
        if isinstance(env, dict):
            for name, inner in env.items():
                if isinstance(inner, str) and SECRET_NAME.search(str(name)):
                    masked(inner)
            return
    mask_exactly(value, MASK_FROM)
    if len(value.split()) > 1:
        mask_exactly("".join(value.split()), MASK_FROM)


def default_secret_store() -> SecretStore:
    """The OS store when keyring has a usable backend, else an in-memory store with a warning."""
    if os.environ.get(STORE_ENV, "").lower() == "memory":
        return MemorySecretStore()
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        if isinstance(keyring.get_keyring(), FailKeyring):
            raise RuntimeError("no usable keyring backend")
        return KeyringSecretStore()
    except Exception as exc:
        log.warning("no OS secret store (%s); keys will live in memory for this run only", exc)
        return MemorySecretStore()
