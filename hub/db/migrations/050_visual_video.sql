-- Stage 29.8а (D55): a video's frames and their rate (Wan2.2-TI2V-5B: 4k+1 frames at 24 a second); a picture's are
-- empty.
ALTER TABLE visual_jobs ADD COLUMN frames INTEGER;
ALTER TABLE visual_jobs ADD COLUMN fps INTEGER;
