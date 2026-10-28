-- 2026-10-28 (the user's word): a chat's own choice of Strata's refusal vector - on, off, or NULL: as the model is set.
ALTER TABLE conversation_context ADD COLUMN refusal_vector INTEGER CHECK (refusal_vector IN (0, 1));
