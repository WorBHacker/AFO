-- Step 9.6a (D43): the programs of this computer and what the autonomous agent may control. A decision is the
-- user's (the desktop app's): allow or deny; none is the same as deny. A program the last look did not find keeps
-- its row and decision (removed_at): installed again, it is decided already.
CREATE TABLE programs (
    id         TEXT PRIMARY KEY,      -- a short hash of `key`: the same program keeps its id
    key        TEXT NOT NULL UNIQUE,  -- 'exe:c:\program files\7-zip\7zfm.exe', 'aumid:microsoft.windowsnotepad_...!app'
    name       TEXT NOT NULL,
    publisher  TEXT NOT NULL DEFAULT '',
    version    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL CHECK (kind IN ('desktop', 'store', 'manual')),
    exe        TEXT NOT NULL DEFAULT '',  -- a desktop program is matched by this exact file
    folder     TEXT NOT NULL DEFAULT '',  -- a Store app is matched by its package folder
    aumid      TEXT NOT NULL DEFAULT '',  -- the AppUserModelID its windows carry, when the Start menu gives one
    decision   TEXT CHECK (decision IN ('allow', 'deny')),
    decided_by TEXT,
    decided_at TEXT,
    fresh      INTEGER NOT NULL DEFAULT 0 CHECK (fresh IN (0, 1)),  -- new since the first look: still to be asked
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    removed_at TEXT
);

CREATE INDEX programs_decision ON programs (decision);
