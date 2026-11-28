-- «Аудио» in the Studio («Стабилизация», portion 5; the user's complaint 15 of 2026-11-27): a voice reading a text,
-- music by its description, a sound by its description - each piece a WAV of its own, relative to data/media.
CREATE TABLE sound_pieces (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('voice', 'music', 'effect')),
    words      TEXT NOT NULL,  -- the text read, or the description
    voice      TEXT NOT NULL DEFAULT '',  -- the voice's id (audio_cpp.VOICES), a voice's piece alone
    seconds    REAL,  -- asked for (music, a sound); when done - how long it is
    take       INTEGER NOT NULL DEFAULT 0,  -- which making of these words: another seed, another sound
    status     TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    error      TEXT NOT NULL DEFAULT '',
    file       TEXT NOT NULL DEFAULT '',
    took       REAL,  -- seconds it took to make
    client     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX sound_pieces_created ON sound_pieces (created_at);
