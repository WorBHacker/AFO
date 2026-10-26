-- Stage 1 core: clients and their tokens, settings, model catalog, conversations, messages, usage, events.
-- Times are ISO 8601 UTC text ('2026-10-26T20:47:28.832Z'). Columns named as JSON hold JSON text.

-- D31: every client (app, Cline, Claude Code, Telegram bot) has its own token; only its SHA-256 is stored.
CREATE TABLE clients (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    token_hash   TEXT NOT NULL UNIQUE,
    cloud        INTEGER NOT NULL DEFAULT 1 CHECK (cloud IN (0, 1)),  -- D32: a client counts as cloud until marked local
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,  -- JSON
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- D5-D7: one catalog for local and cloud models.
CREATE TABLE models (
    id           TEXT PRIMARY KEY,  -- 'llama-swap/flash-next', 'anthropic/claude-sonnet-5-5'
    provider     TEXT NOT NULL,
    model_ref    TEXT NOT NULL,     -- the id the provider expects
    name         TEXT NOT NULL,
    locality     TEXT NOT NULL CHECK (locality IN ('local', 'cloud')),
    capabilities TEXT NOT NULL DEFAULT '{}',  -- JSON: tools, vision, context
    prices       TEXT NOT NULL DEFAULT '{}',  -- JSON, USD per 1M tokens: input, output, cache_read, cache_write
    enabled      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- A chat and an agent run are both conversations (D29).
CREATE TABLE conversations (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('chat', 'agent_run')),
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    archived   INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);

-- D14: messages are only appended. Deleting a whole conversation is allowed; editing a message is not.
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    model_id        TEXT,
    content         TEXT NOT NULL,  -- JSON: blocks in the hub's own format
    provider_raw    TEXT,           -- JSON: the provider's response as received (D7)
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (conversation_id, seq)
);

CREATE TRIGGER messages_append_only BEFORE UPDATE ON messages
BEGIN
    SELECT RAISE(ABORT, 'messages are append-only (D14)');
END;

-- D12: one row per model request, kept forever (D30), also after its conversation is deleted.
CREATE TABLE usage (
    id                  INTEGER PRIMARY KEY,
    ts                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    client_id           TEXT REFERENCES clients (id) ON DELETE SET NULL,
    conversation_id     TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    message_id          INTEGER REFERENCES messages (id) ON DELETE SET NULL,
    model_id            TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0,
    estimated           INTEGER NOT NULL DEFAULT 0 CHECK (estimated IN (0, 1)),  -- client-side estimate (Claude Code)
    meta                TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX usage_ts ON usage (ts);

-- The shared event feed (D29): what happened, in order. Only appended.
CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    type       TEXT NOT NULL,
    project_id TEXT,
    payload    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX events_type ON events (type);

CREATE TRIGGER events_append_only BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
