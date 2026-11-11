"""What the hub keeps on disk, within D30's limits (2026-10-28): the changed files' snapshots stay while a change
younger than the term needs them, the screen snapshots and the terminal logs go past it, every kind stays within its
size (the oldest first); the limits are the user's, through the API and the window."""

import asyncio
import json
import os
import threading
import time

import pytest

from hub import retention
from hub.db import Database, migrate
from tests.test_tools import hub  # noqa: F401 - the hub fixture

DAY = 86400


def aged(path, days_ago, data=b"x" * 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    at = time.time() - days_ago * DAY
    os.utime(path, (at, at))
    return path


def test_limits_are_checked_and_kept(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    assert db.call(retention.current) == {"days": 90, "max_gb": 2.0}  # D30's term, Q4's size per kind
    for days, gb, said in ((3, 1, "от 7"), (90, 0, "от 0.1"), (True, 1, "целое"), (90, "two", "предел")):
        with pytest.raises(retention.RetentionError, match=said):
            db.call(lambda c, d=days, g=gb: retention.save(c, d, g))
    db.call(lambda c: retention.save(c, 30, 1.5))
    assert db.call(retention.current) == {"days": 30, "max_gb": 1.5}
    db.close()


def test_snapshots_live_as_long_as_the_changes_that_need_them(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    folder = tmp_path / "snapshots"
    for name in ("fresh", "stale", "orphan_old", "orphan_new"):
        aged(folder / name, 200 if name != "orphan_new" else 1)  # every file itself is old but one

    def write(conn):
        conn.execute("INSERT INTO projects (id, name, path) VALUES ('p', 'P', 'D:/p')")
        conn.execute(
            "INSERT INTO file_changes (project_id, path, before_hash, after_hash, tool, created_at)"
            " VALUES ('p', 'a.txt', 'stale', 'fresh', 'edit_file', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        )
        conn.execute("INSERT INTO file_changes (project_id, path, before_hash, after_hash, tool, created_at)"
                     " VALUES ('p', 'b.txt', NULL, 'stale', 'write_file', '2026-01-01T00:00:00.000Z')")  # fmt: skip

    db.call(write)
    removed = retention.prune_snapshots(db, folder, days=90, max_bytes=10**9)
    # «stale» is needed by a fresh change (its «before»): it stays with it
    assert removed == 1 and sorted(p.name for p in folder.iterdir()) == ["fresh", "orphan_new", "stale"]
    removed = retention.prune_snapshots(db, folder, days=90, max_bytes=250)  # 300 bytes: one goes
    assert removed == 1 and sorted(p.name for p in folder.iterdir()) == ["fresh", "stale"]  # the oldest by use
    db.close()


def test_screens_and_logs_go_past_the_term_and_over_the_size(tmp_path):
    screens = tmp_path / "computer"
    aged(screens / "run1" / "old.png", 120)
    aged(screens / "run2" / "a.png", 10)
    aged(screens / "run2" / "b.png", 5)
    assert retention.prune_screens(screens, days=90, max_bytes=10**9) == 1
    assert not (screens / "run1").exists()  # a run's folder left empty goes too
    assert retention.prune_screens(screens, days=90, max_bytes=150) == 1
    assert [p.name for p in (screens / "run2").iterdir()] == ["b.png"]

    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    aged(tmp_path / "terminals" / "t1.log", 1)
    db.call(lambda c: c.execute("INSERT INTO terminals (id, title, cwd, command, cols, rows, status, created_by,"
                                " created_at, ended_at) VALUES ('t1', 'T', 'D:/', 'cmd', 80, 24, 'exited', 'app',"
                                " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"))  # fmt: skip
    aged(tmp_path / "snapshots" / "x", 1)
    removed = retention.prune_all(db, tmp_path)
    # 90 days by default
    assert removed == {"snapshots": 0, "screens": 0, "terminals": 1, "files": 0, "copies": 0,
                       "pictures": 0}  # fmt: skip
    used = retention.usage(tmp_path)
    assert used["snapshots"] == {"bytes": 100, "files": 1} and used["terminals"]["files"] == 0
    db.close()


def test_files_sent_with_messages_stay_while_a_message_names_them(tmp_path):
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    named, orphan, fresh = "a" * 64, "b" * 64, "c" * 64
    for name, days in ((named, 400), (orphan, 400), (fresh, 1)):
        aged(tmp_path / "blobs" / name, days)
    db.call(lambda c: c.execute("INSERT INTO conversations (id, kind) VALUES ('c1', 'chat')"))
    content = json.dumps([{"type": "image", "name": "x.png", "media_type": "image/png", "blob": named}])
    db.call(lambda c: c.execute("INSERT INTO messages (conversation_id, seq, role, content, created_at) VALUES"
                                " ('c1', 1, 'user', ?, '2025-01-01T00:00:00Z')", (content,)))  # fmt: skip
    copy = aged(tmp_path / "attachments" / "c1" / "7" / "plan.txt", 400)
    pasted = aged(tmp_path / "pasted" / "картинка.png", 400)
    removed = retention.prune_all(db, tmp_path)
    assert removed["files"] == 1 and removed["copies"] == 2  # the orphan, old; the old copies
    assert sorted(p.name for p in (tmp_path / "blobs").iterdir()) == [named, fresh]
    assert not copy.exists() and not pasted.exists() and not (tmp_path / "attachments" / "c1").exists()
    db.close()


def test_pictures_drawn_go_past_the_term_and_over_the_size(tmp_path):
    """29.4б: data/media - the pictures «Нарисовать» keeps, by month - go like the snapshots (D30); a picture sent
    into a chat lives in the chat (data/blobs), kept with it."""
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    old = aged(tmp_path / "media" / "2026-01" / "old.png", 120)
    fresh = aged(tmp_path / "media" / "2026-11" / "new.png", 2)
    assert retention.prune_all(db, tmp_path)["pictures"] == 1
    assert not old.exists() and not old.parent.exists() and fresh.exists()  # a month left empty goes too
    assert retention.usage(tmp_path)["pictures"] == {"bytes": 100, "files": 1}
    db.close()


def test_the_database_answers_while_the_hub_walks_its_folders(tmp_path, monkeypatch):
    """#7 (2026-11-11): pruning walked every kind's folder holding the database's only lock - on a big data folder
    or a slow disk, seconds to tens of seconds in which none of the hub's calls reached the database: answers being
    saved, events, the window's requests (30 s and it gives up). Now each folder is walked with the database free."""
    db = Database(tmp_path / "hub.db")
    db.call(migrate)
    for kept in ("snapshots/x", "computer/run/s.png", "blobs/" + "a" * 64, "attachments/c1/7/plan.txt",
                 "pasted/p.png", "media/2026-01/old.png", "terminals/t1.log"):  # fmt: skip
        aged(tmp_path / kept, 400)
    db.call(lambda c: c.execute("INSERT INTO terminals (id, title, cwd, command, cols, rows, status, created_by,"
                                " created_at, ended_at) VALUES ('t1', 'T', 'D:/', 'cmd', 80, 24, 'exited', 'app',"
                                " '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')"))  # fmt: skip
    walked, held, others = [], [], []
    files = retention._files

    def answered() -> bool:  # another of the hub's calls to the database, from a thread of its own
        other = threading.Thread(target=db.call, args=(lambda c: c.execute("SELECT 1").fetchone(),))
        others.append(other)
        other.start()
        other.join(2.0)
        return not other.is_alive()

    def walk(folder, pattern):
        walked.append(folder.name)
        if not held and not answered():  # the first one held is enough to tell
            held.append(folder.name)
        return files(folder, pattern)

    monkeypatch.setattr(retention, "_files", walk)
    removed = asyncio.run(retention.Keeper(db, tmp_path).prune())
    for other in others:
        other.join()
    assert not held, f"the database waited while the hub walked data/{held[0]}"
    assert sorted(walked) == ["attachments", "blobs", "computer", "media", "pasted", "snapshots"]
    assert removed == {"snapshots": 1, "screens": 1, "terminals": 1, "files": 1, "copies": 2, "pictures": 1,
                       "goal_refs": 0}  # fmt: skip
    db.close()


def test_the_api_reads_sets_and_prunes(hub):  # noqa: F811
    found = hub.get("/v1/support/retention").json()
    assert (found["days"], found["max_gb"]) == (90, 2.0) and set(found["usage"]) == {"snapshots", "screens",
                                                                                      "terminals", "files",
                                                                                      "pictures"}  # fmt: skip
    assert hub.put("/v1/support/retention", json={"days": 2, "max_gb": 1}).status_code == 422
    saved = hub.put("/v1/support/retention", json={"days": 30, "max_gb": 0.5}).json()
    assert (saved["days"], saved["max_gb"]) == (30, 0.5)
    pruned = hub.post("/v1/support/retention/prune").json()
    assert set(pruned["removed"]) == {"snapshots", "screens", "terminals", "files", "copies", "pictures", "goal_refs"}
