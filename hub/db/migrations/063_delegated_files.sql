-- Stage 61.2: a task's own files - {project_id, path, project} each, JSON: put in its agent's context, with no map of
-- the projects and no live state, so a small model's window stays for the work. NULL: none named.
ALTER TABLE delegated_tasks ADD COLUMN files TEXT;
