CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE CHECK (email IS NULL OR btrim(email) <> ''),
  display_name text CHECK (display_name IS NULL OR btrim(display_name) <> ''),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);

CREATE TABLE IF NOT EXISTS workspaces (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (btrim(name) <> ''),
  created_by_user_id uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz
);

CREATE TABLE IF NOT EXISTS memory_source (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  source_type text NOT NULL,
  storage_object_key text,
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_idempotency_key text NOT NULL,
  preparation_status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
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
  CONSTRAINT memory_source_preparation_status_check CHECK (
    preparation_status IN ('pending', 'processing', 'ready', 'failed')
  ),
  PRIMARY KEY (workspace_id, id),
  CONSTRAINT memory_source_idempotency_key_unique
    UNIQUE (workspace_id, source_idempotency_key)
);

-- Keep this first production migration compatible with databases that used the
-- earlier prototype memory_source table.
ALTER TABLE memory_source
  ADD COLUMN IF NOT EXISTS preparation_status text NOT NULL DEFAULT 'pending';
ALTER TABLE memory_source
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS memory_source_type_created_idx
  ON memory_source (workspace_id, source_type, created_at DESC);
CREATE INDEX IF NOT EXISTS memory_source_metadata_gin_idx
  ON memory_source USING gin (source_metadata);

CREATE TABLE IF NOT EXISTS ingestion_run (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL,
  source_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  result_json jsonb,
  error_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ingestion_run_source_fk
    FOREIGN KEY (workspace_id, source_id)
    REFERENCES memory_source(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT ingestion_run_source_unique UNIQUE (workspace_id, source_id),
  CONSTRAINT ingestion_run_status_check CHECK (
    status IN ('pending', 'processing', 'completed', 'failed')
  ),
  CONSTRAINT ingestion_run_terminal_payload_check CHECK (
    (status = 'completed' AND result_json IS NOT NULL AND error_json IS NULL)
    OR (status = 'failed' AND result_json IS NULL AND error_json IS NOT NULL)
    OR (status IN ('pending', 'processing') AND result_json IS NULL AND error_json IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS ingestion_run_workspace_created_idx
  ON ingestion_run (workspace_id, created_at DESC);

