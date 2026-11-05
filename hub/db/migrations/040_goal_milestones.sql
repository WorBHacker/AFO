-- Stage 11.5 (D44; the user's plan 5, level 2): a goal's stages, in order. The worker says a stage is done («STAGE
-- DONE: …» at the end of a turn); the hub accepts it when its own check finds nothing broken. A stage done keeps the
-- worker's line and where its turns end in the conversation (`closed_seq`): from then on they go to the model as that
-- one line - the stored conversation stays whole (D14). The user may change the stages not done yet; the next note
-- says so (`stages_changed`).
CREATE TABLE goal_milestones (
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    n               INTEGER NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done')),
    summary         TEXT NOT NULL DEFAULT '',  -- the worker's «STAGE DONE» line
    turn            INTEGER,  -- the turn it was done in
    closed_seq      INTEGER,  -- the conversation's last message of that turn
    done_at         TEXT,
    PRIMARY KEY (goal_id, n)
);

ALTER TABLE goals ADD COLUMN stages_changed INTEGER NOT NULL DEFAULT 0;
