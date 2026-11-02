"""Copies of the hub's database (D29, D46): one a day, read while the hub writes, the last few kept; one before
migrations; a restore that keeps what it replaces."""

import asyncio
import importlib
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from hub import backup
from hub.db import Database, migrate

migrations = importlib.import_module("hub.db.migrate")


def rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT title FROM conversations ORDER BY title").fetchall()
    finally:
        conn.close()


def test_one_copy_a_day_while_the_hub_writes(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind, title) VALUES ('a', 'chat', 'first')"))
    folder = tmp_path / "backups"
    day = date(2026, 10, 27)

    made = backup.back_up(db.path, folder, day)
    assert made == folder / "hub-2026-10-27.db"
    assert rows(made) == [("first",)]  # what was in the WAL is in the copy too
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind, title) VALUES ('b', 'chat', 'second')"))
    assert backup.back_up(db.path, folder, day) is None  # today's copy is there: not again
    assert rows(made) == [("first",)]
    assert not list(folder.glob("*.part"))

    later = backup.back_up(db.path, folder, day + timedelta(days=1))
    assert rows(later) == [("first",), ("second",)]
    assert db.call(lambda c: c.execute("SELECT count(*) FROM conversations").fetchone()[0]) == 2  # hub goes on
    db.close()


def test_only_the_last_copies_stay(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "hub-2026-10-01.db").write_bytes(b"old")
    (folder / "notes.txt").write_text("the user's own file")  # not a copy: never touched
    (folder / "hub-latest.db").write_bytes(b"not a dated copy")
    start = date(2026, 10, 20)
    for n in range(4):
        backup.back_up(db.path, folder, start + timedelta(days=n), keep=3)
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["hub-2026-10-21.db", "hub-2026-10-22.db", "hub-2026-10-23.db", "hub-latest.db", "notes.txt"]
    db.close()


def test_a_failed_copy_leaves_the_hub_alone(tmp_path, caplog):
    keeper = backup.DailyBackup(tmp_path / "missing" / "hub.db", tmp_path / "backups", 3600)
    assert asyncio.run(keeper.check()) is None
    assert "daily copy" in caplog.text
    assert not list((tmp_path / "backups").glob("hub-*.db"))


def tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_a_copy_before_migrations_then_a_restore_that_keeps_what_it_replaces(tmp_path, monkeypatch):
    db = Database(tmp_path / "hub.db")
    folder = tmp_path / "backups"
    assert backup.migrate_keeping_a_copy(db, folder)  # a new database: nothing to keep a copy of
    assert not folder.exists() or not list(folder.iterdir())
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind, title) VALUES ('a', 'chat', 'before')"))
    folder.mkdir()
    for n in (1, 2, 3):
        (folder / f"before-v00{n}-20260101-000000.db").write_bytes(b"older")
    real = migrations.available_migrations()
    latest = real[-1][0]

    def newer():
        return [*real, (latest + 1, "newer", "CREATE TABLE newer (x INTEGER);")]

    monkeypatch.setattr(migrations, "available_migrations", newer)
    monkeypatch.setattr(backup, "available_migrations", newer)
    assert backup.migrate_keeping_a_copy(db, folder, datetime(2026, 11, 3, 10, 15)) == [latest + 1]
    copy = folder / f"before-v{latest:03d}-20261103-101500.db"
    assert rows(copy) == [("before",)] and "newer" not in tables(copy)  # the database as it was
    assert sorted(p.name for p in folder.glob("before-v*")) == [
        "before-v002-20260101-000000.db", "before-v003-20260101-000000.db", copy.name]  # fmt: skip
    assert backup.migrate_keeping_a_copy(db, folder) == []  # nothing to apply: no copy either
    assert len(list(folder.glob("before-v*"))) == 3
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind, title) VALUES ('b', 'chat', 'after')"))
    db.close()

    kept = backup.restore(copy, db.path, folder, datetime(2026, 11, 3, 11, 0))
    assert kept == folder / "replaced-20261103-110000.db"
    assert rows(kept) == [("after",), ("before",)] and "newer" in tables(kept)  # the restore can be undone
    assert rows(db.path) == [("before",)] and "newer" not in tables(db.path)
    assert {copy, kept} <= set(backup.restorable(folder))
    assert "не читается" in backup.describe(folder / "before-v002-20260101-000000.db")
    assert f"схема {latest}" in backup.describe(copy)

    # the restored database is the hub's again: the next start migrates it, after a copy
    db = Database(tmp_path / "hub.db")
    assert backup.migrate_keeping_a_copy(db, folder) == [latest + 1]
    db.close()


def test_a_damaged_copy_is_not_restored(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind, title) VALUES ('a', 'chat', 'mine')"))
    db.close()
    folder = tmp_path / "backups"
    folder.mkdir()
    broken = folder / "hub-2026-11-01.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\xff" * 5000)
    with pytest.raises(sqlite3.DatabaseError):
        backup.restore(broken, db.path, folder)
    assert rows(db.path) == [("mine",)] and not list(folder.glob("replaced-*"))
