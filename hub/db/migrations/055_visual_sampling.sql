-- 50.4, the director's scenes: a video's own steps and guidance, said with the request - NULL: the ones the
-- model was installed with (its flags).
ALTER TABLE visual_jobs ADD COLUMN steps INTEGER;
ALTER TABLE visual_jobs ADD COLUMN cfg REAL;
