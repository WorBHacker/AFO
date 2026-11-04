"""API keys and other secrets (D13). On Windows keyring stores them in the Credential Manager.

Secrets never go into settings.json or the database, and their values are never logged or returned by
the API - the API reports only whether a key is set. Which secrets the hub knows, and the password that guards
them, is hub/connections.py.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger("hub.secrets")

SERVICE = "AFO"
OLD_SERVICE = "LocalChat"  # the name before 10.4b: a secret still kept under it moves to SERVICE when first read
# "memory" keeps secrets in the process only: tests and throwaway runs never touch the OS store
STORE_ENV = "LOCALCHAT_SECRETS"

ANTHROPIC_API_KEY = "anthropic_api_key"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...


class KeyringSecretStore:
    """Backed by the OS secret store (Windows Credential Manager via keyring's WinVaultKeyring)."""

    def __init__(self, service: str = SERVICE, old: str | None = OLD_SERVICE):
        self.service = service
        self.old = old

    def get(self, name: str) -> str | None:
        import keyring

        value = keyring.get_password(self.service, name)
        if value is None and self.old:
            value = keyring.get_password(self.old, name)
            if value is not None:  # kept under the old name: moved
                keyring.set_password(self.service, name, value)
                self._drop(self.old, name)
        return value

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> bool:
        """Under the old name too, so a secret never comes back from there."""
        gone = self._drop(self.service, name)
        return (self._drop(self.old, name) if self.old else False) or gone

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
