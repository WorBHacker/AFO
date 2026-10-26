-- Stage 2: the project registry. A project is a folder the hub knows about: its tree, files and git are shown,
-- and later its processes, terminals and context (D4, D22). Removing a project forgets it; the folder stays.

CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL UNIQUE,  -- absolute and resolved (no symlinks, no '..')
    description TEXT NOT NULL DEFAULT '',
    cloud_ok    INTEGER NOT NULL DEFAULT 0 CHECK (cloud_ok IN (0, 1)),  -- D10: may its content go to cloud models
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
