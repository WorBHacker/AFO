import pytest

from common.paths import data_paths
from hub.auth import (
    APP_CLIENT,
    bearer_token,
    create_client,
    ensure_app_token,
    find_client,
    hash_token,
    list_clients,
    revoke_client,
    rotate_token,
)
from hub.db import Database, migrate


@pytest.fixture
def conn():
    database = Database(data_paths().ensure().db)
    database.call(migrate)
    yield database.call(lambda c: c)
    database.close()


def test_create_find_and_cloud_default(conn):
    token = create_client(conn, "cline")
    client = find_client(conn, token)
    assert client is not None
    assert client.name == "cline"
    assert client.cloud  # D32: cloud until marked local
    assert find_client(conn, "wrong-token") is None


def test_only_the_hash_is_stored(conn):
    token = create_client(conn, "cline")
    stored = conn.execute("SELECT token_hash FROM clients WHERE name = 'cline'").fetchone()[0]
    assert stored == hash_token(token)
    assert token not in stored


def test_revoke_and_rotate(conn):
    old = create_client(conn, "claude-code", cloud=True)
    assert revoke_client(conn, "claude-code")
    assert find_client(conn, old) is None
    assert not revoke_client(conn, "claude-code")  # already revoked
    new = rotate_token(conn, "claude-code")
    assert find_client(conn, new) is not None
    assert find_client(conn, old) is None
    with pytest.raises(KeyError):
        rotate_token(conn, "nobody")


def test_app_token_file(conn):
    tokens = data_paths().tokens
    path = ensure_app_token(conn, tokens)
    token = path.read_text(encoding="utf-8")
    app = find_client(conn, token)
    assert app is not None
    assert app.name == APP_CLIENT
    assert not app.cloud
    assert ensure_app_token(conn, tokens) == path
    assert path.read_text(encoding="utf-8") == token  # a valid file is kept as is


def test_missing_app_token_file_gets_a_new_token(conn):
    tokens = data_paths().tokens
    first = ensure_app_token(conn, tokens).read_text(encoding="utf-8")
    (tokens / "app.token").unlink()
    second = ensure_app_token(conn, tokens).read_text(encoding="utf-8")
    assert second != first
    assert find_client(conn, first) is None
    assert find_client(conn, second) is not None
    assert [c.name for c in list_clients(conn)] == [APP_CLIENT]


@pytest.mark.parametrize(
    ("header", "expected"),
    [("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", None), ("Bearer ", None), (None, None), ("", None)],
)
def test_bearer_token(header, expected):
    assert bearer_token(header) == expected
