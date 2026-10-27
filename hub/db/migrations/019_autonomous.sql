-- Stage 9.1 (D40, Q22): the autonomous agent. A run of kind 'autonomous' has a goal and what "done" means, a level
-- of autonomy and a limit of re-plans; its plan is data - versions, each with its steps and why it was made - and
-- it may ask the user a question and wait for the answer. It ends with a report.
ALTER TABLE agent_runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task', 'autonomous'));
ALTER TABLE agent_runs ADD COLUMN goal TEXT;
ALTER TABLE agent_runs ADD COLUMN done_when TEXT;
ALTER TABLE agent_runs ADD COLUMN autonomy TEXT CHECK (autonomy IS NULL OR autonomy IN ('each_step', 'plan', 'free'));
ALTER TABLE agent_runs ADD COLUMN max_replans INTEGER;
ALTER TABLE agent_runs ADD COLUMN planner_model_id TEXT;  -- plans first, then the run's model carries it out
ALTER TABLE agent_runs ADD COLUMN report TEXT;  -- JSON: summary, goal_met, done, not_done, files

CREATE TABLE run_plans (
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',  -- why this plan replaced the one before
    made_by         TEXT NOT NULL,  -- the agent, or the user who changed it
    approved        TEXT,  -- the user approved it (or 'auto': no approval needed)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (conversation_id, version)
);

CREATE TABLE run_steps (
    conversation_id TEXT NOT NULL,
    version         INTEGER NOT NULL,
    n               INTEGER NOT NULL,
    title           TEXT NOT NULL,
    check_how       TEXT NOT NULL DEFAULT '',  -- how the agent will know the step is done
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT '',  -- the result, how it was checked, or why it failed
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (conversation_id, version, n),
    FOREIGN KEY (conversation_id, version) REFERENCES run_plans (conversation_id, version) ON DELETE CASCADE
);

CREATE TABLE run_questions (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    answer          TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'expired')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at     TEXT,
    answered_by     TEXT
);

CREATE INDEX run_questions_pending ON run_questions (status, conversation_id);
