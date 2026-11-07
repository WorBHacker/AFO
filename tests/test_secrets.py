"""The secret store (D13): Windows Credential Manager in the hub, memory in tests. What goes in through the API is
tests/test_connections.py."""

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from hub.secrets import OLD_SERVICE, SERVICE, KeyringSecretStore, MemorySecretStore, default_secret_store

LIMIT = 1280  # characters in one Credential Manager entry: CredWrite refuses more (error 1783, read 2026-11-07)


def test_memory_store():
    store = MemorySecretStore()
    store.set("a", "1")
    assert store.get("a") == "1"
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert isinstance(default_secret_store(), MemorySecretStore)  # tests run with LOCALCHAT_SECRETS=memory


class Vault(KeyringBackend):
    """The Credential Manager as keyring sees it, in memory: an entry over LIMIT characters is refused."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.kept: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.kept.get((service, username))

    def set_password(self, service, username, password):
        if len(password) > LIMIT:
            raise OSError(1783, "CredWrite")
        self.kept[(service, username)] = password

    def delete_password(self, service, username):
        if self.kept.pop((service, username), None) is None:
            raise PasswordDeleteError(username)


@pytest.fixture
def vault():
    before = keyring.get_keyring()
    keyring.set_keyring(fake := Vault())
    yield fake
    keyring.set_keyring(before)


def test_a_value_longer_than_one_entry_is_kept_in_parts(vault):
    store = KeyringSecretStore()
    account = '{"type": "service_account", "private_key": "' + "k" * 2300 + '"}'  # a Google key file is ~2.4K
    store.set("vertex", account)
    assert store.get("vertex") == account
    assert all(len(value) <= LIMIT for value in vault.kept.values()) and len(vault.kept) > 2
    assert {service for service, _ in vault.kept} == {SERVICE}

    store.set("vertex", "short-value-1234")  # replaced by a short one: its parts go
    assert store.get("vertex") == "short-value-1234" and len(vault.kept) == 1
    store.set("vertex", account)
    assert store.delete("vertex") is True
    assert vault.kept == {} and store.get("vertex") is None and store.delete("vertex") is False


def test_a_value_kept_under_the_old_name_moves(vault):
    vault.kept[(OLD_SERVICE, "mcp_env.gh")] = "e" * 1000
    assert KeyringSecretStore().get("mcp_env.gh") == "e" * 1000
    assert vault.kept == {(SERVICE, "mcp_env.gh"): "e" * 1000}
