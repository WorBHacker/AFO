-- Stage 12 (D45): the proving ground and the models' exam. A run is one task for one model on a fresh copy of the
-- task's files - a project and a chat of its own, hidden from the lists (a «scratch» project; the chat archived).
ALTER TABLE projects ADD COLUMN scratch INTEGER NOT NULL DEFAULT 0 CHECK (scratch IN (0, 1));

CREATE TABLE proving_exams (
    id              TEXT PRIMARY KEY,
    models          TEXT NOT NULL,  -- JSON: the model ids, in the table's order
    tasks           TEXT NOT NULL,  -- JSON: the task ids
    judge           INTEGER NOT NULL DEFAULT 0,  -- the model of «Проверка цели» checks the goals too
    status          TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'stopped')),
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ended_at        TEXT
);

CREATE TABLE proving_runs (
    id              TEXT PRIMARY KEY,
    exam_id         TEXT REFERENCES proving_exams (id) ON DELETE CASCADE,  -- NULL: a try («Попробовать»)
    n               INTEGER NOT NULL DEFAULT 0,  -- its place in the exam
    task_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'waiting'
                    CHECK (status IN ('waiting', 'running', 'passed', 'failed', 'error', 'skipped', 'stopped')),
    detail          TEXT NOT NULL DEFAULT '',
    goal_status     TEXT NOT NULL DEFAULT '',
    checks          TEXT NOT NULL DEFAULT '[]',  -- JSON: the hidden checks - n, why, passed, what they said
    turns           INTEGER NOT NULL DEFAULT 0,
    tokens          INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    seconds         REAL NOT NULL DEFAULT 0,
    folder          TEXT NOT NULL,  -- the copy: data/proving/<run>
    project_id      TEXT,
    conversation_id TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at      TEXT,
    ended_at        TEXT
);

CREATE INDEX proving_runs_exam ON proving_runs (exam_id, n);
