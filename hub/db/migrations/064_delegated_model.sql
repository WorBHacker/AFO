-- Stage 61.3: the model a coordinator gave a task, instead of its agent's own; NULL: the agent's. Its name ends in
-- model_id, so a model renamed (36.1) is followed here too.
ALTER TABLE delegated_tasks ADD COLUMN given_model_id TEXT;
