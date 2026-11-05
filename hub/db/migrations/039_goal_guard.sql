-- Stage 11.4 (D44; the user's plan 6 and its idea 3): the goal's guard against going round in circles. Each check
-- keeps the fingerprint of what failed (NULL when nothing did) and how much failed. A check may send the next turn as
-- an investigation - writing and commands refused for that turn (route 'investigate'); the check after that turn
-- runs nothing ('investigated'): nothing could change. The goal remembers that its next turn investigates.
-- SQLite changes a CHECK by rebuilding the table.
ALTER TABLE goals ADD COLUMN investigating INTEGER NOT NULL DEFAULT 0;

CREATE TABLE goal_checks_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    turn            INTEGER NOT NULL,
    route           TEXT NOT NULL CHECK (route IN ('continue', 'last_turn', 'achieved', 'blocked', 'budget', 'paused',
                                                   'investigate', 'investigated')),
    verdict         TEXT CHECK (verdict IS NULL OR verdict IN ('ACHIEVED', 'INCOMPLETE', 'REGRESSED')),
    results         TEXT NOT NULL DEFAULT '[]',  -- JSON: per criterion - n, met, exit code, the last lines
    missing         TEXT NOT NULL DEFAULT '[]',  -- JSON: what is not met yet, in words
    detail          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    fingerprint     TEXT,  -- what failed, without its noise (hub/guard.py); NULL: nothing did
    failing         INTEGER NOT NULL DEFAULT 0  -- how much failed: the commands not met and what else was found
);
INSERT INTO goal_checks_new (id, goal_id, turn, route, verdict, results, missing, detail, created_at)
    SELECT id, goal_id, turn, route, verdict, results, missing, detail, created_at FROM goal_checks;
DROP TABLE goal_checks;
ALTER TABLE goal_checks_new RENAME TO goal_checks;
CREATE INDEX goal_checks_goal ON goal_checks (goal_id, id);
