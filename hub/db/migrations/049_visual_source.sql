-- Stage 29.6б (D55): «По картинке» - a picture drawn from a picture: its source kept beside the job (relative to
-- data/media; the ladder's rungs use it again) and how much to change it (img2img's denoising strength).
ALTER TABLE visual_jobs ADD COLUMN source TEXT NOT NULL DEFAULT '';
ALTER TABLE visual_jobs ADD COLUMN strength REAL;
