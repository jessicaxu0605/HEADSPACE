CREATE TABLE IF NOT EXISTS memory_source (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  source_type text NOT NULL,
  external_uri text,
  data_import_id uuid,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT memory_source_type_check CHECK (
    source_type IN (
      'email', 'text_message', 'pdf', 'pdf_snippet', 'document',
      'document_snippet', 'photo', 'image_region', 'manual_entry',
      'calendar_event', 'structured_import'
    )
  ),
  CONSTRAINT memory_source_metadata_object_check CHECK (
    jsonb_typeof(metadata_json) = 'object'
  ),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, data_import_id)
    REFERENCES data_import(workspace_id, id),
  FOREIGN KEY (workspace_id, origin_action_id)
    REFERENCES graph_action(workspace_id, id),
  FOREIGN KEY (workspace_id, last_modified_action_id)
    REFERENCES graph_action(workspace_id, id)
);

CREATE INDEX IF NOT EXISTS memory_source_type_created_idx
  ON memory_source (workspace_id, source_type, created_at DESC);
CREATE INDEX IF NOT EXISTS memory_source_data_import_idx
  ON memory_source (workspace_id, data_import_id) WHERE data_import_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_source_metadata_gin_idx
  ON memory_source USING gin (metadata_json);
