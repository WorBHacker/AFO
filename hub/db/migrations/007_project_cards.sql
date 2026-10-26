-- Project cards (stage 3, D23, D24): what a model should know about a project, written as a draft by a model and
-- confirmed (and edited) by the user. Every confirmed version is kept, so any edit can be taken back.
CREATE TABLE project_cards (
    id           INTEGER PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,          -- 0 for a draft; confirmed versions count up from 1
    status       TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
    text         TEXT NOT NULL,             -- Markdown
    model_id     TEXT,                      -- who wrote the draft it came from; NULL for the user's own text
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX project_cards_by_project ON project_cards (project_id, status, version);
