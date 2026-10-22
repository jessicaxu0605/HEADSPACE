-- Local username/password authentication and persisted, revocable sessions.
-- Existing development users are retained but cannot log in until credentials
-- are explicitly assigned; "!" is not a valid Argon2 password hash.

ALTER TABLE users ADD COLUMN IF NOT EXISTS login text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;

UPDATE users
SET
  login = COALESCE(login, email, 'legacy-' || id::text),
  name = COALESCE(name, display_name, 'Legacy user'),
  password_hash = COALESCE(password_hash, '!')
WHERE login IS NULL OR name IS NULL OR password_hash IS NULL;

ALTER TABLE users ALTER COLUMN login SET NOT NULL;
ALTER TABLE users ALTER COLUMN name SET NOT NULL;
ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_login_not_blank;
ALTER TABLE users ADD CONSTRAINT users_login_not_blank
  CHECK (btrim(login) <> '');
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_name_not_blank;
ALTER TABLE users ADD CONSTRAINT users_name_not_blank
  CHECK (btrim(name) <> '');
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_password_hash_not_blank;
ALTER TABLE users ADD CONSTRAINT users_password_hash_not_blank
  CHECK (btrim(password_hash) <> '');

CREATE UNIQUE INDEX IF NOT EXISTS users_login_unique_idx
  ON users (lower(login));

CREATE TABLE IF NOT EXISTS workspace_member (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
  joined_at timestamptz,
  removed_at timestamptz,
  PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS workspace_member_user_idx
  ON workspace_member (user_id) WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS auth_session (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  active_workspace_id uuid REFERENCES workspaces(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_used_at timestamptz,
  revoked_at timestamptz,
  CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS auth_session_user_active_idx
  ON auth_session (user_id, expires_at DESC) WHERE revoked_at IS NULL;
