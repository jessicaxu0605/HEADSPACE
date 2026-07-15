CREATE TABLE IF NOT EXISTS memory_evidence (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  edge_id uuid NOT NULL,
  source_id uuid NOT NULL,
  raw_content text NOT NULL CHECK (raw_content <> ''),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  strength_boost double precision NOT NULL DEFAULT 0 CHECK (strength_boost >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, edge_id)
    REFERENCES memory_edge(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, source_id)
    REFERENCES memory_source(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, origin_action_id)
    REFERENCES graph_action(workspace_id, id),
  FOREIGN KEY (workspace_id, last_modified_action_id)
    REFERENCES graph_action(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS memory_evidence_edge_idx ON memory_evidence (workspace_id, edge_id);
CREATE INDEX IF NOT EXISTS memory_evidence_source_idx ON memory_evidence (workspace_id, source_id);
