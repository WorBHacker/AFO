-- 52.6, a film with sound: its video model films each scene with it (a model with an audio VAE; 058), the director
-- writes what is heard in a scene, the joined film keeps the scenes' sound.
ALTER TABLE films ADD COLUMN sound INTEGER NOT NULL DEFAULT 1;  -- the film's switch: 1 - filmed with sound
ALTER TABLE film_scenes ADD COLUMN sound_words TEXT NOT NULL DEFAULT '';  -- what is heard in the scene; '' - as the model hears it
