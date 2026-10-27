-- Stage 9.6 (D41): the journal of computer control - each action an autonomous run took in a window of an allowed
-- program, what came of it, and a snapshot of the window after it (a file in data/computer/<run>/, kept as D30 says).
CREATE TABLE computer_actions (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    window_title    TEXT NOT NULL,
    program         TEXT NOT NULL,
    action          TEXT NOT NULL,  -- JSON: the action as the model asked it (typed text left out)
    result          TEXT NOT NULL,
    snapshot        TEXT NOT NULL DEFAULT '',  -- the file, or '' when none was taken
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX computer_actions_run ON computer_actions (conversation_id, id);
