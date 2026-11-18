-- 52.3, a video with sound: LTX films it when its audio VAE is among the model's files. Such a clip is the engine's
-- WebM (<job>.webm), with an MP4 and a poster (<job>.jpg) beside it; a silent one stays a WebP.
ALTER TABLE visual_jobs ADD COLUMN sound INTEGER NOT NULL DEFAULT 0;  -- 1 - filmed with sound
ALTER TABLE visual_jobs ADD COLUMN sound_words TEXT NOT NULL DEFAULT '';  -- what is heard, as the person said it
