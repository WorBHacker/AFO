-- Projects attached to a conversation (stage 3, D27): the chat's focus. Its tools reach only these projects, and
-- a cloud model only those of them that allow the cloud (D10).
CREATE TABLE conversation_projects (
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    PRIMARY KEY (conversation_id, project_id)
);
