-- Terminals the hub runs (stage 2, D4, D30): what was started where, how it ended, and how much output it left.
-- The output itself is a file, data/terminals/<id>.log; it is kept 90 days and within a total size (D30), the row
-- stays. A terminal that was running when the hub stopped is 'lost': the hub owned its pseudo-console.
CREATE TABLE terminals (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects (id) ON DELETE SET NULL,
    title       TEXT NOT NULL DEFAULT '',
    command     TEXT NOT NULL,  -- the program and its arguments, as Windows quotes them
    cwd         TEXT NOT NULL,
    cols        INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    pid         INTEGER,
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'exited', 'lost')),
    exit_code   INTEGER,
    created_by  TEXT NOT NULL,  -- the client that started it
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at    TEXT,
    log_bytes   INTEGER NOT NULL DEFAULT 0,
    log_deleted INTEGER NOT NULL DEFAULT 0 CHECK (log_deleted IN (0, 1))
);

CREATE INDEX terminals_by_project ON terminals (project_id, created_at);
