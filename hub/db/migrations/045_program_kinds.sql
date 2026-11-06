-- Stage 25.2 (D54, D43): two more kinds of programs. 'game' - a Steam game, found by Steam's libraries
-- (libraryfolders.vdf, appmanifest_*.acf) and matched by its folder; 'window' - a program no look knows (portable,
-- or a launcher's real window: Git Bash draws through mintty.exe) seen with a window open, offered like a new one.
-- SQLite keeps a CHECK as it was made, so the table is made again with the wider one, its rows kept.
CREATE TABLE programs_new (
    id         TEXT PRIMARY KEY,
    key        TEXT NOT NULL UNIQUE,  -- 'exe:...', 'aumid:...', 'steam:292030'
    name       TEXT NOT NULL,
    publisher  TEXT NOT NULL DEFAULT '',
    version    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL CHECK (kind IN ('desktop', 'store', 'manual', 'game', 'window')),
    exe        TEXT NOT NULL DEFAULT '',
    folder     TEXT NOT NULL DEFAULT '',  -- a Store app and a game are matched by their folder
    aumid      TEXT NOT NULL DEFAULT '',
    decision   TEXT CHECK (decision IN ('allow', 'deny')),
    decided_by TEXT,
    decided_at TEXT,
    fresh      INTEGER NOT NULL DEFAULT 0 CHECK (fresh IN (0, 1)),
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    removed_at TEXT
);
INSERT INTO programs_new (id, key, name, publisher, version, kind, exe, folder, aumid, decision, decided_by,
                          decided_at, fresh, first_seen, last_seen, removed_at)
    SELECT id, key, name, publisher, version, kind, exe, folder, aumid, decision, decided_by, decided_at, fresh,
           first_seen, last_seen, removed_at FROM programs;
DROP TABLE programs;
ALTER TABLE programs_new RENAME TO programs;
CREATE INDEX programs_decision ON programs (decision);
