-- 53.3, 53.4: the sound added to a film when it is joined - the narrator's voice reading each scene's line, music
-- under the whole film (hub/sound.py, hub/director.py).
ALTER TABLE films ADD COLUMN voice TEXT NOT NULL DEFAULT '';  -- the narrator's voice (audio_cpp.VOICES); '' - none
ALTER TABLE films ADD COLUMN music TEXT NOT NULL DEFAULT '';  -- '' - none, 'made' - by music_words, 'file' - music_file
ALTER TABLE films ADD COLUMN music_words TEXT NOT NULL DEFAULT '';  -- what music is made, in English
ALTER TABLE films ADD COLUMN music_file TEXT NOT NULL DEFAULT '';  -- the user's own, copied into data/media
ALTER TABLE film_scenes ADD COLUMN say TEXT NOT NULL DEFAULT '';  -- the narrator's line read while the scene goes
