-- An answer may now cover the whole project (approvals.scope 'project'): SQLite changes a CHECK by rebuilding.
CREATE TABLE approvals_new (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    conversation_id TEXT REFERENCES conversations (id) ON DELETE CASCADE,
    project_id      TEXT REFERENCES projects (id) ON DELETE SET NULL,
    client_id       TEXT REFERENCES clients (id) ON DELETE SET NULL,
    tool            TEXT NOT NULL,
    input           TEXT NOT NULL,
    grant_key       TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'allowed', 'denied', 'expired')),
    scope           TEXT CHECK (scope IN ('once', 'chat', 'project', 'auto')),
    comment         TEXT,
    decided_at      TEXT,
    decided_by      TEXT
);
INSERT INTO approvals_new SELECT * FROM approvals;
DROP TABLE approvals;
ALTER TABLE approvals_new RENAME TO approvals;
CREATE INDEX approvals_pending ON approvals (status) WHERE status = 'pending';
CREATE INDEX approvals_conversation ON approvals (conversation_id, grant_key);

-- Stage 5 (D17): the hub's models may write files, each write asked (or allowed by a rule) and kept so it can be
-- undone. A change keeps the file before and after it by hash (the contents are in data/snapshots, one copy each).
CREATE TABLE file_changes (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    project_id      TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    path            TEXT NOT NULL,  -- relative to the project, with /
    before_hash     TEXT,           -- NULL: the file was created
    after_hash      TEXT NOT NULL,
    tool            TEXT NOT NULL,  -- write_file, edit_file
    conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    approval_id     TEXT REFERENCES approvals (id) ON DELETE SET NULL,
    reverted_at     TEXT
);

CREATE INDEX file_changes_project ON file_changes (project_id, created_at);

-- "Always for this project" answers (D17): what they cover - a tool, or for a command the command itself. Destructive
-- commands are never covered.
CREATE TABLE permission_rules (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    project_id TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    grant_key  TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE (project_id, grant_key)
);

-- A chat's rights mode (D17): read only, ask (the default), or trusted (writes in its projects without asking).
ALTER TABLE conversation_context ADD COLUMN mode TEXT NOT NULL DEFAULT 'ask' CHECK (mode IN ('read_only', 'ask', 'trusted'));
