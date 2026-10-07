CREATE TABLE IF NOT EXISTS memory_edge (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  from_node_id uuid NOT NULL,
  to_node_id uuid NOT NULL,
  relation_type_id uuid NOT NULL REFERENCES relation_type(id) ON DELETE RESTRICT,
  strength double precision NOT NULL DEFAULT 0.5 CHECK (strength BETWEEN 0 AND 1),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  valid_from timestamptz,
  valid_to timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT memory_edge_valid_range_check CHECK (
    valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from
  ),
  CONSTRAINT memory_edge_deleted_fields_check CHECK (
    deleted_reason IS NULL OR deleted_at IS NOT NULL
  ),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, from_node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, to_node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, origin_action_id)
    REFERENCES graph_action(workspace_id, id),
  FOREIGN KEY (workspace_id, last_modified_action_id)
    REFERENCES graph_action(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS memory_edge_from_relation_idx
  ON memory_edge (workspace_id, from_node_id, relation_type_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_edge_to_relation_idx
  ON memory_edge (workspace_id, to_node_id, relation_type_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_edge_pair_idx
  ON memory_edge (workspace_id, from_node_id, to_node_id) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS memory_edge_set_updated_at ON memory_edge;
CREATE TRIGGER memory_edge_set_updated_at
BEFORE UPDATE ON memory_edge
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
