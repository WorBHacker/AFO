-- Stage 22.3 (Q20 as the user decided it, D54): a cloud model reads other programs' windows only in a task where
-- that is turned on. The switch in «Папки и сайты для агента» (setting agent.screen_cloud) is what a new task
-- starts with; a task that began without it stays so. Runs from before: off, as the switch was by default.
ALTER TABLE agent_runs ADD COLUMN screen_cloud INTEGER NOT NULL DEFAULT 0 CHECK (screen_cloud IN (0, 1));
