-- Derived edge search projections (Design V1: edge_search_projection).
-- Rebuilt from projection_rebuild_queue; never the source of truth. The vector
-- width matches node_search_projection and the default nomic-embed-text model.

CREATE TABLE IF NOT EXISTS edge_search_projection (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  edge_id uuid NOT NULL,
  embedding_model text NOT NULL CHECK (btrim(embedding_model) <> ''),
  projection_version integer NOT NULL CHECK (projection_version > 0),
  search_text text NOT NULL,
  embedding_text text NOT NULL,
  embedding vector(768) NOT NULL,
  source_hash text NOT NULL,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, edge_id, embedding_model, projection_version),
  FOREIGN KEY (workspace_id, edge_id)
    REFERENCES memory_edge(workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_search_projection_current_idx
  ON edge_search_projection (workspace_id, embedding_model, projection_version DESC);

CREATE INDEX IF NOT EXISTS edge_search_projection_embedding_idx
  ON edge_search_projection USING hnsw (embedding vector_cosine_ops);

DROP TRIGGER IF EXISTS edge_search_projection_set_updated_at ON edge_search_projection;
CREATE TRIGGER edge_search_projection_set_updated_at
BEFORE UPDATE ON edge_search_projection
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
