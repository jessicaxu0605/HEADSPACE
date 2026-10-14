-- Support bounded, index-backed exact and fuzzy canonical node search.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS memory_node_name_trgm_idx
  ON memory_node USING gin (lower(name) gin_trgm_ops)
  WHERE deleted_at IS NULL AND merged_into_node_id IS NULL;

CREATE INDEX IF NOT EXISTS node_identifier_text_trgm_idx
  ON node_identifier USING gin (lower(identifier_text) gin_trgm_ops)
  WHERE deleted_at IS NULL;
