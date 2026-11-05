-- Stage 16.3 (D50): a task of a batch may carry criteria - how the hub checks it when its agent ends (the goal's
-- checks: commands, then the judge). Unmet, the agent goes another round with what the check found, up to a limit;
-- then a person is needed ('escalated'). The board keeps the criteria, the rounds and the last verdict.
ALTER TABLE delegated_tasks ADD COLUMN criteria TEXT;  -- JSON, as hub/goals.py check_criteria gives them; NULL: none
ALTER TABLE delegated_tasks ADD COLUMN rounds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE delegated_tasks ADD COLUMN verdict TEXT;  -- ACHIEVED / INCOMPLETE / REGRESSED; NULL: not judged
ALTER TABLE delegated_tasks ADD COLUMN feedback TEXT;  -- the judge's word to the agent, the last one
