CREATE TABLE IF NOT EXISTS node_identifier (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL,
  identifier_text text NOT NULL CHECK (btrim(identifier_text) <> ''),
  scope text NOT NULL DEFAULT 'global' CHECK (btrim(scope) <> ''),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT node_identifier_deleted_fields_check CHECK (
    deleted_reason IS NULL OR deleted_at IS NOT NULL
  ),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, origin_action_id)
    REFERENCES graph_action(workspace_id, id),
  FOREIGN KEY (workspace_id, last_modified_action_id)
    REFERENCES graph_action(workspace_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS node_identifier_active_unique_idx
  ON node_identifier (workspace_id, node_id, scope, lower(identifier_text)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS node_identifier_lookup_idx
  ON node_identifier (workspace_id, scope, lower(identifier_text)) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS node_identifier_set_updated_at ON node_identifier;
CREATE TRIGGER node_identifier_set_updated_at
BEFORE UPDATE ON node_identifier
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
