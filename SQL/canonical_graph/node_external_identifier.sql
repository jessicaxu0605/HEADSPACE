CREATE TABLE IF NOT EXISTS node_external_identifier (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL,
  provider text NOT NULL,
  external_id text NOT NULL CHECK (btrim(external_id) <> ''),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT node_external_identifier_provider_check CHECK (
    provider IN ('google_contacts', 'gmail', 'slack', 'instagram', 'calendar', 'manual_import')
  ),
  CONSTRAINT node_external_identifier_deleted_fields_check CHECK (
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

CREATE UNIQUE INDEX IF NOT EXISTS node_external_identifier_active_unique_idx
  ON node_external_identifier (workspace_id, provider, external_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS node_external_identifier_node_idx
  ON node_external_identifier (workspace_id, node_id) WHERE deleted_at IS NULL;
