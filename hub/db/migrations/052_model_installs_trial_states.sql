-- Stage 34 (D57): two states of an install. 'trialwait' - its trial waits for the graphics card (tuning measures, a
-- chat answers; 34.4); 'added' - a model from the drives written into llama-swap's config and not tried yet: the trial
-- is the person's button (34.5). SQLite keeps a CHECK as it was made, so the table is made again, its rows kept. No
-- table refers to model_installs.
CREATE TABLE model_installs_new (
    id          TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,
    revision    TEXT NOT NULL DEFAULT 'main',
    quant       TEXT NOT NULL,
    files       TEXT NOT NULL,
    folder      TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    purposes    TEXT NOT NULL DEFAULT '[]',
    license     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL CHECK (status IN ('queued', 'downloading', 'paused', 'trialwait', 'configuring', 'trial',
                                                'added', 'done', 'failed', 'cancelled', 'removed')),
    done_bytes  INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    plan        TEXT NOT NULL DEFAULT '{}',
    trial       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    own_files   INTEGER NOT NULL DEFAULT 1
);
INSERT INTO model_installs_new (id, repo, revision, quant, files, folder, model_id, name, purposes, license, status,
                                done_bytes, total_bytes, error, plan, trial, created_at, updated_at, own_files)
    SELECT id, repo, revision, quant, files, folder, model_id, name, purposes, license, status, done_bytes,
           total_bytes, error, plan, trial, created_at, updated_at, own_files FROM model_installs;
DROP TABLE model_installs;
ALTER TABLE model_installs_new RENAME TO model_installs;
CREATE INDEX model_installs_model ON model_installs (model_id);
