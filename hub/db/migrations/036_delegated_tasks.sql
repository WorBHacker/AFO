-- Stage 16.1 (D50): a coordinator gives a batch of tasks to other agents at once. Each task of the batch is a row:
-- its name in the batch, the agent, what to do, what it waits for, how it went and the run that did it - the board
-- the coordinator and the window read, apart from the conversations.
CREATE TABLE delegated_tasks (
    coordinator TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    batch INTEGER NOT NULL,  -- which delegate_tasks call of the coordinator, from 1
    position INTEGER NOT NULL,  -- its place in the batch as the coordinator wrote it
    agent TEXT NOT NULL,
    prompt TEXT NOT NULL,
    dependencies TEXT NOT NULL DEFAULT '[]',  -- task_ids of the same batch, JSON
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | cancelled | skipped
    run_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    answer TEXT,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at TEXT,
    ended_at TEXT,
    PRIMARY KEY (coordinator, task_id)
);

CREATE INDEX delegated_tasks_run ON delegated_tasks (run_id);
