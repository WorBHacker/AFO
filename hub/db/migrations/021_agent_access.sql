-- Stage 9.4 (D40, Q21): whether an autonomous task's Python scripts may reach the network (off by default).
ALTER TABLE agent_runs ADD COLUMN network INTEGER NOT NULL DEFAULT 0 CHECK (network IN (0, 1));
