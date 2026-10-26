-- Links between projects (stage 3, D25): the hub proposes them from evidence, the user confirms each one.
CREATE TABLE project_links (
    id          INTEGER PRIMARY KEY,
    a_id        TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,  -- the one that uses / depends
    b_id        TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('uses', 'provides', 'shares', 'deploys_with', 'same_product', 'other')),
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    evidence    TEXT NOT NULL DEFAULT '[]',  -- what the hub found, as lines a person can check
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (a_id, b_id, kind)
);
