CREATE TABLE IF NOT EXISTS node_search_projection (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  node_id uuid NOT NULL,
  projection_type text NOT NULL,
  embedding_model text NOT NULL CHECK (btrim(embedding_model) <> ''),
  search_text text NOT NULL,
  embedding_text text NOT NULL,
  embedding vector NOT NULL,
  projection_version integer NOT NULL CHECK (projection_version > 0),
  source_hash text,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT node_search_projection_type_check CHECK (
    projection_type IN ('identity', 'profile')
  ),
  PRIMARY KEY (user_id, node_id, projection_type, embedding_model, projection_version),
  FOREIGN KEY (user_id, node_id)
    REFERENCES memory_node(user_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS node_search_projection_current_idx
  ON node_search_projection (user_id, projection_type, embedding_model, projection_version DESC);

DROP TRIGGER IF EXISTS node_search_projection_set_updated_at ON node_search_projection;
CREATE TRIGGER node_search_projection_set_updated_at
BEFORE UPDATE ON node_search_projection
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
