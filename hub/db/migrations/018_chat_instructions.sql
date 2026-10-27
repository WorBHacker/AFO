-- Stage 8.3 (D39): a chat's own instructions - after the general and the model's system prompt, before an agent's
-- role - and its preset of the answer's parameters over the model's ('' - as the model has them).
ALTER TABLE conversation_context ADD COLUMN instructions TEXT NOT NULL DEFAULT '';
ALTER TABLE conversation_context ADD COLUMN preset TEXT NOT NULL DEFAULT '';
