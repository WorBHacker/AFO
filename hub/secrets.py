"""API keys and other secrets (D13). On Windows keyring stores them in the Credential Manager.

Secrets never go into settings.json or the database, and their values are never logged or returned by
the API - the API reports only whether a key is set.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger("hub.secrets")

SERVICE = "LocalChat"
# "memory" keeps secrets in the process only: tests and throwaway runs never touch the OS store
STORE_ENV = "LOCALCHAT_SECRETS"

ANTHROPIC_API_KEY = "anthropic_api_key"
# the secrets the hub knows, with what each one is for; the API and the CLI accept only these names
KNOWN_SECRETS: dict[str, str] = {
    ANTHROPIC_API_KEY: "Anthropic API key (Claude)",
    "telegram_bot_token": "Токен Telegram-бота для подтверждений (D21)",
}


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...


class KeyringSecretStore:
    """Backed by the OS secret store (Windows Credential Manager via keyring's WinVaultKeyring)."""

    def __init__(self, service: str = SERVICE):
        self.service = service

    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> bool:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, name)
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
