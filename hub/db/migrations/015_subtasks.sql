-- Stage 6 (D20): a coordinator agent gives parts of its task to other agents. A sub-run remembers its coordinator's
-- run; what it answered goes back to the coordinator as its tool's result.
ALTER TABLE agent_runs ADD COLUMN parent_run TEXT REFERENCES conversations (id) ON DELETE SET NULL;

CREATE INDEX agent_runs_parent ON agent_runs (parent_run);
