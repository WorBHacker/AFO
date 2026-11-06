-- Stage 26.1 (the audit's Local History): every version of a project's file the hub saw - the watcher's sight of
-- any change (another program, a command, Claude Code, git) and the hub's own writes - so any change can be taken
-- back. The content is in data/snapshots by its hash, one copy each, shared with file_changes (D29).
CREATE TABLE file_versions (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    project_id TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    path       TEXT NOT NULL,  -- relative to the project, with /
    hash       TEXT,           -- NULL: the file was deleted
    size       INTEGER NOT NULL DEFAULT 0,
    change_id  INTEGER REFERENCES file_changes (id) ON DELETE SET NULL  -- the hub's own write that made it
);

CREATE INDEX file_versions_path ON file_versions (project_id, path, id);
