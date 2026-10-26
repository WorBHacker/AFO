-- Notes (stage 3, D22 L4, D23): short facts and decisions about a project that a model should always know -
-- "deploy only from main", "the API keys are in the vault". Written by the user, kept in the hub.
CREATE TABLE notes (
    id          INTEGER PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX notes_by_project ON notes (project_id, id);
