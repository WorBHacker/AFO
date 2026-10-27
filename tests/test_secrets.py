"""The secret store (D13): Windows Credential Manager in the hub, memory in tests. What goes in through the API is
tests/test_connections.py."""

from hub.secrets import MemorySecretStore, default_secret_store


def test_memory_store():
    store = MemorySecretStore()
    store.set("a", "1")
    assert store.get("a") == "1"
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert isinstance(default_secret_store(), MemorySecretStore)  # tests run with LOCALCHAT_SECRETS=memory
