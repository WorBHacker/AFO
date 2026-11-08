-- Stage 33.1 (D56): «Полный» joins the autonomous agent's levels - nothing is asked. SQLite keeps a CHECK as it was
-- made, so the table is made again with the wider one, its rows kept. No table refers to agent_runs.
CREATE TABLE agent_runs_new (
    conversation_id  TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    profile_id       TEXT REFERENCES agent_profiles (id) ON DELETE SET NULL,
    profile_version  INTEGER,
    profile_name     TEXT NOT NULL,
    instructions     TEXT NOT NULL DEFAULT '',  -- as the profile had them when the run began
    model_id         TEXT NOT NULL,
    max_steps        INTEGER NOT NULL,
    max_minutes      INTEGER NOT NULL,
    budget_usd       REAL,
    task             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'done', 'error', 'cancelled')),
    detail           TEXT,  -- why it ended (an error, a limit)
    steps            INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at       TEXT,
    ended_at         TEXT,
    parent_run       TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    kind             TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task', 'autonomous')),
    goal             TEXT,
    done_when        TEXT,
    autonomy         TEXT CHECK (autonomy IS NULL OR autonomy IN ('each_step', 'plan', 'free', 'full')),
    max_replans      INTEGER,
    planner_model_id TEXT,
    report           TEXT,
    network          INTEGER NOT NULL DEFAULT 0 CHECK (network IN (0, 1)),
    schedule_id      TEXT,
    refusal_vector   INTEGER CHECK (refusal_vector IN (0, 1)),
    screen_cloud     INTEGER NOT NULL DEFAULT 0 CHECK (screen_cloud IN (0, 1)),
    baselines        TEXT
);
INSERT INTO agent_runs_new (conversation_id, profile_id, profile_version, profile_name, instructions, model_id,
                            max_steps, max_minutes, budget_usd, task, status, detail, steps, cost_usd, created_at,
                            started_at, ended_at, parent_run, kind, goal, done_when, autonomy, max_replans,
                            planner_model_id, report, network, schedule_id, refusal_vector, screen_cloud, baselines)
    SELECT conversation_id, profile_id, profile_version, profile_name, instructions, model_id, max_steps,
           max_minutes, budget_usd, task, status, detail, steps, cost_usd, created_at, started_at, ended_at,
           parent_run, kind, goal, done_when, autonomy, max_replans, planner_model_id, report, network, schedule_id,
           refusal_vector, screen_cloud, baselines FROM agent_runs;
DROP TABLE agent_runs;
ALTER TABLE agent_runs_new RENAME TO agent_runs;
CREATE INDEX agent_runs_status ON agent_runs (status, created_at);
CREATE INDEX agent_runs_parent ON agent_runs (parent_run);
