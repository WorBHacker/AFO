-- 57.4: «говорит в кадре» - the narrator's line of such a scene goes under the lips of the person in its frame when
-- the film is joined: the scene's clip through «Губы под голос» (MuseTalk; hub/director.py, hub/enhance.py).
ALTER TABLE film_scenes ADD COLUMN speaks INTEGER NOT NULL DEFAULT 0;
-- JSON {"job": the Studio's job of the clip with the lips made, "key": by which clip and line}; '' - none yet
ALTER TABLE film_scenes ADD COLUMN lips TEXT NOT NULL DEFAULT '';
