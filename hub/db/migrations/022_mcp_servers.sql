-- Stage 9.5 (D42): outside MCP servers the user added in Connections - started by a command (stdio) or reached at an
-- address (Streamable HTTP) - and the class of action the user gave a tool of theirs (else the server's own hints
-- decide: read, write, command, network, send, destructive). A server's environment (its API keys) is in the secret
-- store, not here.
CREATE TABLE mcp_servers (
    name      TEXT PRIMARY KEY,
    transport TEXT NOT NULL CHECK (transport IN ('stdio', 'http')),
    command   TEXT NOT NULL,  -- the program, or the address
    args      TEXT NOT NULL DEFAULT '[]',  -- JSON list
    enabled   INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE mcp_tool_classes (
    server TEXT NOT NULL REFERENCES mcp_servers (name) ON DELETE CASCADE,
    tool   TEXT NOT NULL,
    class  TEXT NOT NULL CHECK (class IN ('read', 'write', 'command', 'network', 'send', 'destructive')),
    set_at TEXT NOT NULL,
    PRIMARY KEY (server, tool)
);
