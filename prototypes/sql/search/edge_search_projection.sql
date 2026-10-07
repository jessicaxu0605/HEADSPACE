CREATE TABLE IF NOT EXISTS edge_search_projection (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  edge_id uuid NOT NULL,
  embedding_model text NOT NULL CHECK (btrim(embedding_model) <> ''),
  search_text text NOT NULL,
  embedding_text text NOT NULL,
  embedding vector NOT NULL,
  projection_version integer NOT NULL CHECK (projection_version > 0),
  source_hash text,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, edge_id, embedding_model, projection_version),
  FOREIGN KEY (workspace_id, edge_id)
    REFERENCES memory_edge(workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_search_projection_current_idx
  ON edge_search_projection (workspace_id, embedding_model, projection_version DESC);

DROP TRIGGER IF EXISTS edge_search_projection_set_updated_at ON edge_search_projection;
CREATE TRIGGER edge_search_projection_set_updated_at
BEFORE UPDATE ON edge_search_projection
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
