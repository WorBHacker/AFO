-- 54.5, «Улучшить ролик»: a job of the Studio's queue that improves a video. `enhance` - JSON {source: the video's
-- file, relative to data/media; job: the Studio's job it came from ('' - a film's joined video); fps: the source's
-- frames a second; size: sharp | detail | null; motion: fps60 | slow2 | slow4 | null}; '' - a job that films or draws.
-- Its result is a new MP4 (<job>.mp4, a film's - film-<film>-<job>.mp4: kept as long as the film) with a poster
-- (<job>.jpg) beside; the source stays as it was.
ALTER TABLE visual_jobs ADD COLUMN enhance TEXT NOT NULL DEFAULT '';
ALTER TABLE visual_jobs ADD COLUMN film_id TEXT;  -- the film whose joined video it improves; NULL - none
