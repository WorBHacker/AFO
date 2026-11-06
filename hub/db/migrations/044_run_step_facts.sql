-- Stage 25.1 (D54, D40): an autonomous agent's step is checked against what it actually changed, not only its
-- words. A step keeps where each git project's files stood when it began (a tree, hub/judge.py) and when (the hub's
-- own writes since then, for projects without git); at its end, the files it changed by the hub's diff and what of
-- that breaks the condition «готово» as the hub checks it. The run keeps where the files stood as its first plan
-- was saved - nothing acts before it: the whole run's diff, for its report and the judge.
ALTER TABLE run_steps ADD COLUMN base TEXT;  -- JSON {project_id: tree}
ALTER TABLE run_steps ADD COLUMN started_at TEXT;
ALTER TABLE run_steps ADD COLUMN files TEXT;  -- JSON [{path, status, added, removed}]; NULL: not seen (no projects)
ALTER TABLE run_steps ADD COLUMN mismatch TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_runs ADD COLUMN baselines TEXT;  -- JSON {project_id: tree}, as the first plan was saved
