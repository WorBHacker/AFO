import sqlite3
import sys

import pytest

from common.paths import data_paths
from hub.db import Database, available_migrations, current_version, migrate

# hub.db re-exports the function `migrate`, which hides the submodule of the same name
migrate_module = sys.modules["hub.db.migrate"]


@pytest.fixture
def db():
    database = Database(data_paths().ensure().db)
    yield database
    database.close()


def test_fresh_database_gets_every_migration(db):
    latest = available_migrations()[-1][0]
    assert db.call(migrate) == list(range(1, latest + 1))
    assert db.call(current_version) == latest
    tables = {
        r[0] for r in db.call(lambda c: c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    }
    assert {"clients", "settings", "models", "conversations", "messages", "usage", "events"} <= tables


def test_second_run_applies_nothing(db):
    db.call(migrate)
    assert db.call(migrate) == []


def test_wal_and_foreign_keys_are_on(db):
    assert db.call(lambda c: c.execute("PRAGMA journal_mode").fetchone()[0]) == "wal"
    assert db.call(lambda c: c.execute("PRAGMA foreign_keys").fetchone()[0]) == 1


def _conversation_with_message(conn):
    conn.execute("INSERT INTO conversations (id, kind) VALUES ('c1', 'chat')")
    conn.execute("INSERT INTO messages (conversation_id, seq, role, content) VALUES ('c1', 1, 'user', '[]')")
    conn.execute("INSERT INTO usage (conversation_id, message_id, model_id) VALUES ('c1', 1, 'llama-swap/coder')")


def test_messages_cannot_be_edited(db):
    db.call(migrate)
    db.call(_conversation_with_message)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.call(lambda c: c.execute("UPDATE messages SET content = '[\"changed\"]'"))


def test_deleting_a_conversation_keeps_its_usage(db):
    db.call(migrate)
    db.call(_conversation_with_message)
    db.call(lambda c: c.execute("DELETE FROM conversations WHERE id = 'c1'"))
    assert db.call(lambda c: c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]) == 0
    row = db.call(lambda c: c.execute("SELECT conversation_id, message_id, model_id FROM usage").fetchone())
    assert tuple(row) == (None, None, "llama-swap/coder")


def test_events_cannot_be_edited(db):
    db.call(migrate)
    db.call(lambda c: c.execute("INSERT INTO events (type) VALUES ('test')"))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.call(lambda c: c.execute("UPDATE events SET type = 'other'"))


def test_failed_migration_is_rolled_back(db, monkeypatch):
    real = available_migrations()
    broken = (real[-1][0] + 1, "broken", "CREATE TABLE half_done (x INTEGER);\nTHIS IS NOT SQL;")
    monkeypatch.setattr(migrate_module, "available_migrations", lambda: [*real, broken])
    with pytest.raises(sqlite3.Error):
        db.call(migrate)
    assert db.call(current_version) == real[-1][0]
    assert db.call(lambda c: c.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchone()) is None


def test_transaction_rolls_back_on_error(db):
    db.call(migrate)
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('a', '1')")
        raise RuntimeError("stop")
    assert db.call(lambda c: c.execute("SELECT COUNT(*) FROM settings").fetchone()[0]) == 0


def test_the_data_folder_and_localchats_own(tmp_path, monkeypatch):
    """10.3: LOCALCHAT_DATA_DIR; a checkout that keeps its data in <project>/data keeps it; else LocalChat's folder."""
    from common import paths

    monkeypatch.delenv(paths.DATA_DIR_ENV)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "checkout")
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    assert paths.data_paths().root == tmp_path / "home" / "data"
    assert paths.engines_home() == tmp_path / "home" / "engines"
    (tmp_path / "checkout" / "data").mkdir(parents=True)
    assert paths.data_paths().root == tmp_path / "checkout" / "data"
    monkeypatch.delenv(paths.HOME_ENV)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    if paths.os.name == "nt":
        assert paths.app_home() == tmp_path / "appdata" / "LocalChat"


def test_models_go_into_localchats_own_model_folder(tmp_path, monkeypatch):
    """2026-10-28 (the user's word): the installer's default place is the `model` folder in LocalChat's own folder;
    LOCALCHAT_MODELS_DIR overrides it; a program folder one may not write to gives LocalChat's app folder."""
    from common import paths

    monkeypatch.delenv(paths.MODELS_ENV)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "checkout")
    (tmp_path / "checkout").mkdir()
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "home"))
    assert paths.models_home() == tmp_path / "checkout" / "model"
    monkeypatch.setattr(paths.os, "access", lambda path, mode: False)  # under Program Files
    assert paths.models_home() == tmp_path / "home" / "model"
    monkeypatch.setenv(paths.MODELS_ENV, str(tmp_path / "mine"))
    assert paths.models_home() == tmp_path / "mine"
