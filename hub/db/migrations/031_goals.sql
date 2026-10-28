-- Stage 11.1 (D44, Q26): a chat's goal - a contract kept apart from its messages: what is wanted, the criteria the
-- hub checks after each turn, hard constraints, the limits and what was used of them. At most one open goal per chat
-- (active, paused, blocked for the human, parked at its limit); achieved and cleared ones stay as history.
CREATE TABLE goals (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    objective       TEXT NOT NULL,
    constraints     TEXT NOT NULL DEFAULT '[]',  -- JSON: the hard constraints, as the user wrote them
    model_id        TEXT NOT NULL,  -- the worker: the chat's model that carries the goal
    effort          TEXT NOT NULL DEFAULT 'medium',
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'paused', 'blocked', 'budget', 'achieved', 'cleared')),
    detail          TEXT NOT NULL DEFAULT '',  -- why it is paused, blocked or parked
    max_turns       INTEGER NOT NULL,
    max_tokens      INTEGER NOT NULL,
    max_seconds     INTEGER NOT NULL,
    soft_ratio      REAL NOT NULL,  -- from this share of a limit on: the last turn
    turns_used      INTEGER NOT NULL DEFAULT 0,
    tokens_used     INTEGER NOT NULL DEFAULT 0,  -- new tokens: input without cache re-reads, and output
    seconds_used    REAL NOT NULL DEFAULT 0,  -- the turns' own time, not the waits between them
    cost_usd        REAL NOT NULL DEFAULT 0,
    last_turn       INTEGER NOT NULL DEFAULT 0,  -- the last turn before the limit was given
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at        TEXT
);

CREATE UNIQUE INDEX goals_open ON goals (conversation_id) WHERE status IN ('active', 'paused', 'blocked', 'budget');

CREATE TABLE goal_criteria (
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    n               INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('command', 'semantic')),
    project_id      TEXT,  -- command: the project it runs in
    command         TEXT,
    path            TEXT NOT NULL DEFAULT '',  -- command: a folder inside the project
    expected_exit_code INTEGER,
    timeout_s       INTEGER,
    rule            TEXT,  -- semantic: what must be true, in words (the judge reads it, 11.2)
    verified        INTEGER NOT NULL DEFAULT 0,  -- the last check found it met
    PRIMARY KEY (goal_id, n)
);

-- what each check after a turn found and where the goal went from there; only appended (D14)
CREATE TABLE goal_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    turn            INTEGER NOT NULL,
    route           TEXT NOT NULL CHECK (route IN ('continue', 'last_turn', 'achieved', 'blocked', 'budget', 'paused')),
    verdict         TEXT CHECK (verdict IS NULL OR verdict IN ('ACHIEVED', 'INCOMPLETE', 'REGRESSED')),
    results         TEXT NOT NULL DEFAULT '[]',  -- JSON: per criterion - n, met, exit code, the last lines
    missing         TEXT NOT NULL DEFAULT '[]',  -- JSON: what is not met yet, in words
    detail          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX goal_checks_goal ON goal_checks (goal_id, id);

-- what the user wrote while the goal worked: read at the start of the next turn
CREATE TABLE goal_steering (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    by              TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    taken_at        TEXT  -- the turn that read it
);
