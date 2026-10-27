-- Stage 5 (D16): the hub's own agents. A profile says who the agent is and what it may do; each save is a new
-- version, kept. A run is a task given to a profile: a conversation of kind 'agent_run', with its status here.
CREATE TABLE agent_profiles (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    version      INTEGER NOT NULL DEFAULT 1,
    instructions TEXT NOT NULL DEFAULT '',  -- the role: what the agent is for and how it should work
    model_id     TEXT NOT NULL,
    tools        TEXT NOT NULL DEFAULT '[]',  -- JSON: the tool names it gets; [] - all of the hub's
    mode         TEXT NOT NULL DEFAULT 'ask' CHECK (mode IN ('read_only', 'ask', 'trusted')),  -- D17
    project_ids  TEXT NOT NULL DEFAULT '[]',  -- JSON: the projects it works with
    max_steps    INTEGER NOT NULL DEFAULT 30,
    max_minutes  INTEGER NOT NULL DEFAULT 30,
    budget_usd   REAL,  -- a task's limit for paid models (D12); NULL - none
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE agent_profile_versions (
    profile_id TEXT NOT NULL REFERENCES agent_profiles (id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    data       TEXT NOT NULL,  -- JSON: the profile as it was saved
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (profile_id, version)
);

CREATE TABLE agent_runs (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    profile_id      TEXT REFERENCES agent_profiles (id) ON DELETE SET NULL,
    profile_version INTEGER,
    profile_name    TEXT NOT NULL,
    instructions    TEXT NOT NULL DEFAULT '',  -- as the profile had them when the run began
    model_id        TEXT NOT NULL,
    max_steps       INTEGER NOT NULL,
    max_minutes     INTEGER NOT NULL,
    budget_usd      REAL,
    task            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'done', 'error', 'cancelled')),
    detail          TEXT,  -- why it ended (an error, a limit)
    steps           INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at      TEXT,
    ended_at        TEXT
);

CREATE INDEX agent_runs_status ON agent_runs (status, created_at);
