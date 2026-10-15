-- Derived node search projections (Design V1: node_search_projection).
-- Rebuilt from projection_rebuild_queue; never the source of truth.
--
-- The vector width is fixed so the column can carry an HNSW index. It matches
-- the default nomic-embed-text model; switching to a model with a different
-- width needs a new migration.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS node_search_projection (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  node_id uuid NOT NULL,
  projection_type text NOT NULL CHECK (projection_type IN ('identity', 'profile')),
  embedding_model text NOT NULL CHECK (btrim(embedding_model) <> ''),
  projection_version integer NOT NULL CHECK (projection_version > 0),
  search_text text NOT NULL,
  embedding_text text NOT NULL,
  embedding vector(768) NOT NULL,
  source_hash text NOT NULL,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, node_id, projection_type, embedding_model, projection_version),
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS node_search_projection_current_idx
  ON node_search_projection (workspace_id, projection_type, embedding_model, projection_version DESC);

CREATE INDEX IF NOT EXISTS node_search_projection_embedding_idx
  ON node_search_projection USING hnsw (embedding vector_cosine_ops);

DROP TRIGGER IF EXISTS node_search_projection_set_updated_at ON node_search_projection;
CREATE TRIGGER node_search_projection_set_updated_at
BEFORE UPDATE ON node_search_projection
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
