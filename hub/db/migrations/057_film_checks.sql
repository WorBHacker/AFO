-- 51.1, the director looks at each scene filmed (hub/director.py): three of its frames and the product's photo go
-- to the director's model, which says «good» or why it is not; a scene that is not is filmed anew, with other words.
ALTER TABLE films ADD COLUMN checks INTEGER NOT NULL DEFAULT 1;  -- the film's switch: 1 - its scenes are looked at
ALTER TABLE films ADD COLUMN checking INTEGER NOT NULL DEFAULT 0;  -- the scene being looked at now; 0 - none
ALTER TABLE film_scenes ADD COLUMN cfg REAL;  -- the guidance the look asked for; NULL - the video model's own
ALTER TABLE film_scenes ADD COLUMN tries INTEGER NOT NULL DEFAULT 0;  -- times the look had it filmed anew
-- '': not looked at (the switch off, the look did not come out); ok; reject: the take that stays is not good
ALTER TABLE film_scenes ADD COLUMN verdict TEXT NOT NULL DEFAULT '' CHECK (verdict IN ('', 'ok', 'reject'));
ALTER TABLE film_scenes ADD COLUMN reason TEXT NOT NULL DEFAULT '';  -- why not, in the user's language
