-- What a chat's context includes (stage 3, D22 "inspector"): the map and the live state can be switched off, and
-- files can be pinned so that they always come with the context. Applies from the next message on: the context
-- already in the conversation stays as the model saw it (D14).
CREATE TABLE conversation_context (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    map             INTEGER NOT NULL DEFAULT 1 CHECK (map IN (0, 1)),
    live            INTEGER NOT NULL DEFAULT 1 CHECK (live IN (0, 1)),
    pinned          TEXT NOT NULL DEFAULT '[]'  -- [{"project_id": ..., "path": ...}]
);
