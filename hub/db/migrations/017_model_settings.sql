-- Stage 8 (D39): what the user set for each model, as versions - the latest is the one in force, the old ones stay
-- (an answer's usage names the version it was made with). model_id '' is the general row (the system prompt for
-- every model).
CREATE TABLE model_settings (
    model_id   TEXT NOT NULL,
    version    INTEGER NOT NULL CHECK (version > 0),
    settings   TEXT NOT NULL,  -- JSON: hidden, name, capabilities, prices, prices_asof, params, preset, effort,
                               --       system_prompt
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_by TEXT,
    PRIMARY KEY (model_id, version)
);
