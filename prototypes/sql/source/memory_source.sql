CREATE TABLE IF NOT EXISTS memory_source (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  source_type text NOT NULL,
  storage_object_key text,
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_idempotency_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT memory_source_type_check CHECK (
    source_type IN (
      'email',
      'text_message',
      'document',
      'pdf',
      'photo',
      'calendar_event',
      'manual_entry',
      'structured_import'
    )
  ),
  CONSTRAINT memory_source_source_metadata_object_check CHECK (
    jsonb_typeof(source_metadata) = 'object'
  ),
  PRIMARY KEY (workspace_id, id),
  CONSTRAINT memory_source_idempotency_key_unique
    UNIQUE (workspace_id, source_idempotency_key)
);

CREATE INDEX IF NOT EXISTS memory_source_type_created_idx
  ON memory_source (workspace_id, source_type, created_at DESC);
CREATE INDEX IF NOT EXISTS memory_source_metadata_gin_idx
  ON memory_source USING gin (source_metadata);
