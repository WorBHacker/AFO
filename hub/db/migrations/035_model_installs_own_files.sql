-- Stage 15.1 (D49): a model found on the person's drives is added to llama-swap where it lies, without a copy. Its
-- files are theirs: removing the model takes its config entry away and never its files (0). Downloaded by AFO: 1.
ALTER TABLE model_installs ADD COLUMN own_files INTEGER NOT NULL DEFAULT 1;
