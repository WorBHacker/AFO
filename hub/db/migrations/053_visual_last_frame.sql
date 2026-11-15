-- Stage 48.5: a video by two frames - its last frame's PNG beside the job (the first one is `source`, 29.6б); a picture's
-- and a video by words have none.
ALTER TABLE visual_jobs ADD COLUMN last_frame TEXT NOT NULL DEFAULT '';
