-- The workspace a user last opened is remembered on their membership, so it
-- follows them across devices and disappears with the membership. Signup marks
-- the default workspace as last used.

ALTER TABLE workspace_member ADD COLUMN IF NOT EXISTS last_used_at timestamptz;
