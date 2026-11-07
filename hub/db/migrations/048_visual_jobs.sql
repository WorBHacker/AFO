-- Stage 29.4 (D55): the pictures the hub makes - one job each, run one at a time through llama-swap; the file is in
-- data/media. A failure goes one rung lower by itself (a smaller size, a lighter model): `tries` keeps each rung.
CREATE TABLE visual_jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL DEFAULT 'image' CHECK (kind IN ('image', 'video')),
    prompt          TEXT NOT NULL,
    negative        TEXT NOT NULL DEFAULT '',
    model_id        TEXT NOT NULL,  -- the picture model's id in llama-swap's config
    place           TEXT NOT NULL DEFAULT 'local' CHECK (place IN ('local', 'cloud')),
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    seed            INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    error           TEXT NOT NULL DEFAULT '',
    file            TEXT NOT NULL DEFAULT '',  -- the result, relative to data/media
    seconds         REAL,
    cost_usd        REAL NOT NULL DEFAULT 0,
    tries           TEXT NOT NULL DEFAULT '[]',  -- JSON: [{model_id, width, height, ok, error, seconds}]
    client          TEXT NOT NULL DEFAULT '',  -- who asked (the window, a chat's tool)
    conversation_id TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX visual_jobs_created ON visual_jobs (created_at);
