-- Stage 11.2 (D44, Q26): the judge - a model that only reads and checks a goal against its contract once the hub's
-- own checks pass. The goal remembers which model judges it, and where each git project's files stood when it began:
-- the diff the judge reads runs from there (a tree written through a separate index - no commit, no branch).
ALTER TABLE goals ADD COLUMN judge_model_id TEXT;  -- NULL: none chosen (criteria in words then wait for the human)

CREATE TABLE goal_baselines (
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL,
    tree            TEXT NOT NULL,  -- git's tree of the working files (tracked and new, without the ignored)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (goal_id, project_id)
);
