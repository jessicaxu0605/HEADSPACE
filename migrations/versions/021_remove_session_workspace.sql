-- Sessions authenticate users, not workspace selections. Workspace access is
-- authorized per request through workspace_member.

ALTER TABLE auth_session DROP COLUMN IF EXISTS active_workspace_id;
