-- Stage 9.3 (D40, Q24): the autonomous agent's memory - a run's own notes, the facts kept for good (each with its
-- source and date, seen and changed by the user; personal ones only with the user's approval, never for a cloud
-- model), and skills: a finished task's plan the user chose to keep. Searched by SQLite's full text (FTS5, D26).
CREATE TABLE run_notes (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX run_notes_run ON run_notes (conversation_id, id);

CREATE TABLE memory_facts (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL,
    about      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,  -- run:<id>, or user
    personal   INTEGER NOT NULL DEFAULT 0 CHECK (personal IN (0, 1)),
    cloud_ok   INTEGER NOT NULL DEFAULT 0 CHECK (cloud_ok IN (0, 1)),  -- a cloud model may read it
    deleted    INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),  -- gone for the user; the row stays (D14)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE VIRTUAL TABLE memory_facts_fts USING fts5(text, about, content='memory_facts', content_rowid='id',
                                                 tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER memory_facts_in AFTER INSERT ON memory_facts BEGIN
    INSERT INTO memory_facts_fts (rowid, text, about) VALUES (new.id, new.text, new.about);
END;
CREATE TRIGGER memory_facts_up AFTER UPDATE OF text, about ON memory_facts BEGIN
    INSERT INTO memory_facts_fts (memory_facts_fts, rowid, text, about) VALUES ('delete', old.id, old.text, old.about);
    INSERT INTO memory_facts_fts (rowid, text, about) VALUES (new.id, new.text, new.about);
END;

CREATE TABLE skills (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    goal       TEXT NOT NULL,
    steps      TEXT NOT NULL,  -- JSON: [{title, check}]
    tools      TEXT NOT NULL DEFAULT '[]',  -- JSON: the tools the task used
    source_run TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    uses       INTEGER NOT NULL DEFAULT 0,  -- how often it was given to a new task as a hint
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE VIRTUAL TABLE skills_fts USING fts5(name, goal, content='skills', content_rowid='id',
                                           tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER skills_in AFTER INSERT ON skills BEGIN
    INSERT INTO skills_fts (rowid, name, goal) VALUES (new.id, new.name, new.goal);
END;
CREATE TRIGGER skills_out AFTER DELETE ON skills BEGIN
    INSERT INTO skills_fts (skills_fts, rowid, name, goal) VALUES ('delete', old.id, old.name, old.goal);
END;
