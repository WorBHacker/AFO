-- 53.13, 53.14: what the listener heard in a scene's sound and whether it is right (hub/hearing.py, hub/director.py).
-- JSON: {"own": {"job", "heard", "bad"} - the sound the video model filmed, "made": {"file", "heard", "bad", "takes"}
-- - the one the engine of sound made}; '' - nothing has been heard yet.
ALTER TABLE film_scenes ADD COLUMN heard TEXT NOT NULL DEFAULT '';
