-- The tool definitions a conversation was first sent, kept for its life (D14): Claude's thinking blocks are bound
-- to the tools they were made with, so a conversation's tool list never changes - new tools reach new conversations.
-- JSON, or NULL until the first request with tools. 'v1' marks conversations from before this column that may have
-- been sent the seven reading tools of step 3.2 (they had context, or Claude answered in them): they keep those.
ALTER TABLE conversations ADD COLUMN tools TEXT;

UPDATE conversations SET tools = 'v1'
WHERE id IN (
    SELECT conversation_id FROM messages
    WHERE role = 'system' OR (role = 'assistant' AND model_id LIKE 'anthropic/%')
);
