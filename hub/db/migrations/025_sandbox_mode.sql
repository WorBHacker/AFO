-- Stage 9.4b (D17): «Песочница» joins a chat's rights. SQLite keeps a CHECK as it was made, so the table is made
-- again with the wider one, its rows kept.
CREATE TABLE conversation_context_new (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    map             INTEGER NOT NULL DEFAULT 1 CHECK (map IN (0, 1)),
    live            INTEGER NOT NULL DEFAULT 1 CHECK (live IN (0, 1)),
    pinned          TEXT NOT NULL DEFAULT '[]',  -- [{"project_id": ..., "path": ...}]
    mode            TEXT NOT NULL DEFAULT 'ask' CHECK (mode IN ('read_only', 'ask', 'sandbox', 'trusted')),
    instructions    TEXT NOT NULL DEFAULT '',
    preset          TEXT NOT NULL DEFAULT ''
);
INSERT INTO conversation_context_new (conversation_id, map, live, pinned, mode, instructions, preset)
    SELECT conversation_id, map, live, pinned, mode, instructions, preset FROM conversation_context;
DROP TABLE conversation_context;
ALTER TABLE conversation_context_new RENAME TO conversation_context;
