CREATE TABLE IF NOT EXISTS workspace_member (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
  joined_at timestamptz,
  removed_at timestamptz,
  last_used_at timestamptz,
  PRIMARY KEY (workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS workspace_member_user_idx
  ON workspace_member (user_id) WHERE removed_at IS NULL;
