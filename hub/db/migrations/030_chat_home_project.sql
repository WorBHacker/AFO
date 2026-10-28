-- 2026-10-28 (the user's word): a chat begun in «Проекты» belongs there - its project's session, listed under that
-- project and not among «Чаты». NULL: an ordinary chat (projects attached to it or not). A project removed: its
-- sessions become ordinary chats.
ALTER TABLE conversations ADD COLUMN home_project TEXT REFERENCES projects (id) ON DELETE SET NULL;
