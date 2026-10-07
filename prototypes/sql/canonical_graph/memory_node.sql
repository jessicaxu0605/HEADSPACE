CREATE TABLE IF NOT EXISTS memory_node (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_type text NOT NULL CHECK (
    node_type IN ('person', 'location', 'date', 'task', 'concept')
  ),
  name text NOT NULL CHECK (btrim(name) <> ''),
  summary text,
  importance double precision NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  merged_into_node_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT memory_node_deleted_fields_check CHECK (
    deleted_reason IS NULL OR deleted_at IS NOT NULL
  ),
  CONSTRAINT memory_node_not_merged_into_self_check CHECK (
    merged_into_node_id IS DISTINCT FROM id
  ),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, merged_into_node_id)
    REFERENCES memory_node(workspace_id, id),
  FOREIGN KEY (workspace_id, origin_action_id)
    REFERENCES graph_action(workspace_id, id),
  FOREIGN KEY (workspace_id, last_modified_action_id)
    REFERENCES graph_action(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS memory_node_type_idx
  ON memory_node (workspace_id, node_type) WHERE deleted_at IS NULL AND merged_into_node_id IS NULL;
CREATE INDEX IF NOT EXISTS memory_node_name_idx
  ON memory_node (workspace_id, lower(name)) WHERE deleted_at IS NULL AND merged_into_node_id IS NULL;
CREATE INDEX IF NOT EXISTS memory_node_merged_into_idx
  ON memory_node (workspace_id, merged_into_node_id) WHERE merged_into_node_id IS NOT NULL;

DROP TRIGGER IF EXISTS memory_node_set_updated_at ON memory_node;
CREATE TRIGGER memory_node_set_updated_at
BEFORE UPDATE ON memory_node
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
