"""The daily copy of the hub's database (D29): one copy a day, read while the hub writes, the last few kept."""

import asyncio
import sqlite3
from datetime import date, timedelta

from hub import backup
from hub.db import Database, migrate


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
