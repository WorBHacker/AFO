-- Stage 25.3 (D54, D25): the ports each project's processes listened on, with when they were last seen. The port
-- evidence of a link (A calls localhost:P, a process in B's folder listens on P) held only while B's process ran;
-- kept here, it lives after the process stops.
CREATE TABLE project_ports (
    project_id TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    port       INTEGER NOT NULL,
    process    TEXT NOT NULL,  -- the listening process's name, as last seen
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,  -- when the ports last changed with it listening; for a closed port, when it closed
    PRIMARY KEY (project_id, port)
);
