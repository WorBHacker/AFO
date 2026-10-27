-- Stage 4 (D17, D18): the approval queue. An agent asks before an action and waits; the user answers in the app
-- (later also from Telegram, D21). Claude Code's permission requests come here through the hub's MCP tool
-- approval_prompt. Rows stay as the log of who allowed what.
CREATE TABLE approvals (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    conversation_id TEXT REFERENCES conversations (id) ON DELETE CASCADE,  -- the chat whose run asks; NULL: a client's own session
    project_id      TEXT REFERENCES projects (id) ON DELETE SET NULL,
    client_id       TEXT REFERENCES clients (id) ON DELETE SET NULL,  -- who asks
    tool            TEXT NOT NULL,  -- the agent's tool: Bash, Edit, Write, WebFetch, mcp__...
    input           TEXT NOT NULL,  -- JSON: what it wants to do
    grant_key       TEXT,           -- what an answer "for this chat" covers: the tool, or for a command the command itself
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'allowed', 'denied', 'expired')),
    scope           TEXT CHECK (scope IN ('once', 'chat', 'auto')),  -- allowed this time, for the chat, or by such an answer
    comment         TEXT,           -- a denial's reason, passed on to the agent
    decided_at      TEXT,
    decided_by      TEXT            -- the client that answered, or 'hub' for an answer it gave itself
);

CREATE INDEX approvals_pending ON approvals (status) WHERE status = 'pending';
CREATE INDEX approvals_conversation ON approvals (conversation_id, grant_key);

-- The external agent's own session behind a chat: Claude Code continues it with --resume <session id> (D8).
ALTER TABLE conversations ADD COLUMN agent_session TEXT;
