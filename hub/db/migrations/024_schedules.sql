-- Stage 9.7 (Q23): autonomous goals the hub starts on a schedule (every day, on days of the week, every N hours) or
-- on an event (a new file in a folder, a message to the user's bot); quiet hours are a setting (agent.quiet). A run
-- remembers the schedule that started it.
CREATE TABLE schedules (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    goal          TEXT NOT NULL,  -- JSON: the goal it starts (goal, done_when, model, autonomy, limits...)
    starts_on     TEXT NOT NULL,  -- JSON: the trigger {kind: time | file | telegram, ...}
    computer      INTEGER NOT NULL DEFAULT 0 CHECK (computer IN (0, 1)),  -- its runs may control the computer (9.6)
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    next_at       TEXT,  -- local time of the next start (a time trigger)
    waiting_until TEXT,  -- local time: a start waits for the quiet hours' end
    pending       TEXT NOT NULL DEFAULT '[]',  -- JSON: events not yet started on (kept through the quiet hours)
    last_run_id   TEXT,
    last_run_at   TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

ALTER TABLE agent_runs ADD COLUMN schedule_id TEXT;
