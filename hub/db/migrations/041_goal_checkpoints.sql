-- Stage 11.6 (D44; the user's plan, idea 2 «shadow git refs»): a goal's checkpoints - where each git project's
-- working files stood at the goal's start and at each stage done: a commit of the hub's own kept by the ref
-- refs/localchat/goals/<goal>/<name> (hub/checkpoints.py). A dead end goes back to the last one not gone back to yet
-- (route 'rolled_back'): in «Песочница» at once, in the project after a card. SQLite changes a CHECK by rebuilding.
CREATE TABLE goal_checkpoints (
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    name            TEXT NOT NULL,  -- start, stage-1, ...
    project_id      TEXT NOT NULL,
    commit_id       TEXT NOT NULL,
    tree            TEXT NOT NULL,
    turn            INTEGER NOT NULL DEFAULT 0,
    rolled_back     INTEGER NOT NULL DEFAULT 0,  -- the goal went back to it once: not again
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (goal_id, name, project_id)
);

CREATE TABLE goal_checks_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    turn            INTEGER NOT NULL,
    route           TEXT NOT NULL CHECK (route IN ('continue', 'last_turn', 'achieved', 'blocked', 'budget', 'paused',
                                                   'investigate', 'investigated', 'rolled_back')),
    verdict         TEXT CHECK (verdict IS NULL OR verdict IN ('ACHIEVED', 'INCOMPLETE', 'REGRESSED')),
    results         TEXT NOT NULL DEFAULT '[]',
    missing         TEXT NOT NULL DEFAULT '[]',
    detail          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    fingerprint     TEXT,
    failing         INTEGER NOT NULL DEFAULT 0
);
INSERT INTO goal_checks_new SELECT * FROM goal_checks;
DROP TABLE goal_checks;
ALTER TABLE goal_checks_new RENAME TO goal_checks;
CREATE INDEX goal_checks_goal ON goal_checks (goal_id, id);
