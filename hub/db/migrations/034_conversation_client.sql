-- Stage 13.3 (D46, D31): who began a conversation. A client other than the window sees and continues only its own;
-- a conversation the window or the hub began (NULL - every one made before this) is the window's.
ALTER TABLE conversations ADD COLUMN client_id TEXT REFERENCES clients (id) ON DELETE SET NULL;
