-- Stage 10.1 (D37): local models the hub installs from Hugging Face - each install's files, where they go, how far
-- the download got, the launch settings it wrote into llama-swap's config and its trial run. A model installed by
-- LocalChat is one with status 'done'; its row is how it is removed again (the files and its config block).
CREATE TABLE model_installs (
    id          TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,  -- "author/name" on Hugging Face
    revision    TEXT NOT NULL DEFAULT 'main',  -- the commit its files came from
    quant       TEXT NOT NULL,  -- the variant: Q4_K_M, Q8_0...
    files       TEXT NOT NULL,  -- JSON: [{path (in the repo), size, sha256, role: model | mmproj, done}]
    folder      TEXT NOT NULL,  -- where its files go
    model_id    TEXT NOT NULL,  -- its id in llama-swap's config
    name        TEXT NOT NULL,
    purposes    TEXT NOT NULL DEFAULT '[]',  -- JSON: chat, code, vision
    license     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL CHECK (status IN ('queued', 'downloading', 'paused', 'configuring', 'trial', 'done',
                                                'failed', 'cancelled', 'removed')),
    done_bytes  INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    plan        TEXT NOT NULL DEFAULT '{}',  -- JSON: the launch settings written (context, gpu_layers, cache_type...)
    trial       TEXT NOT NULL DEFAULT '{}',  -- JSON: the trial run (seconds, tok_s, tries)
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX model_installs_model ON model_installs (model_id);
