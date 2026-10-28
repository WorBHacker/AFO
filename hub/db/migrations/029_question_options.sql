-- 2026-10-28 (the user's word): a question from a model or an agent comes with answers to pick with one click -
-- [{"label": ..., "description": ...}], JSON; NULL: none offered (the user writes the answer).
ALTER TABLE run_questions ADD COLUMN options TEXT;
