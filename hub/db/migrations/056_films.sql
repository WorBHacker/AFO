-- 50.5, «Режиссёр»: a film - an idea, a length, the product's photo - and its scenes, written by a model (the
-- storyboard) and filmed one after another by the Studio's video model; each scene's clip is a job of visual_jobs.
-- The files lie in data/media: the photo (film-<id>-photo.png), the scenes' clips (their jobs'), the joined film
-- (film-<id>.mp4); the cleanup of old pictures leaves a film's files alone while the film is there (hub/retention.py).
CREATE TABLE films (
    id          TEXT PRIMARY KEY,
    idea        TEXT NOT NULL,  -- what the film is about, as the user wrote it
    seconds     INTEGER NOT NULL,  -- the length asked for
    shape       TEXT NOT NULL DEFAULT 'wide' CHECK (shape IN ('wide', 'tall', 'square')),
    photo       TEXT NOT NULL DEFAULT '',  -- the product's photo, relative to data/media; '' - none
    director    TEXT NOT NULL DEFAULT '',  -- the model that wrote the storyboard
    model_id    TEXT NOT NULL DEFAULT '',  -- the video model that films it; '' - the first that can
    -- draft: no storyboard yet; writing: the director is asked; ready: scenes there, none being filmed;
    -- filming: scenes go one after another; joining: ffmpeg puts them together
    status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'writing', 'ready', 'filming', 'joining')),
    error       TEXT NOT NULL DEFAULT '',  -- why the last thing asked did not come out, in words
    file        TEXT NOT NULL DEFAULT '',  -- the joined film, relative to data/media; '' - not joined (or scenes changed)
    fade        REAL NOT NULL DEFAULT 0,  -- a cross-fade at each joint, seconds; 0 - cut to cut
    tokens      INTEGER NOT NULL DEFAULT 0,  -- the director's questions: what they took
    cost_usd    REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX films_created ON films (created_at);

CREATE TABLE film_scenes (
    film_id     TEXT NOT NULL REFERENCES films (id) ON DELETE CASCADE,
    n           INTEGER NOT NULL,  -- from 1, the order in the film
    -- text: by its words alone; picture: from the product's photo; continue: from the last frame of the scene
    -- before; frames: from that frame to the product's photo
    mode        TEXT NOT NULL CHECK (mode IN ('text', 'picture', 'continue', 'frames')),
    words       TEXT NOT NULL DEFAULT '',  -- what happens, in the user's language: the scene's card
    prompt      TEXT NOT NULL,  -- for the video model, in English
    frames      INTEGER NOT NULL,  -- step·k+1, as the family's VAE takes them
    seed        INTEGER,  -- NULL: a new one each time it is filmed
    job_id      TEXT,  -- visual_jobs: the scene's clip, the latest
    -- new: not filmed; filming: its job waits or runs; done; failed; stale: filmed, but the scene it goes on from
    -- has been filmed anew since
    status      TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'filming', 'done', 'failed', 'stale')),
    error       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (film_id, n)
);

CREATE INDEX film_scenes_job ON film_scenes (job_id);
