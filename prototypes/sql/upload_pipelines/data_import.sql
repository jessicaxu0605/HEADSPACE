CREATE TABLE IF NOT EXISTS data_import (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  created_by_user_id uuid NOT NULL REFERENCES users(id),
  source_format text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  failed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  CONSTRAINT data_import_source_format_check CHECK (
    source_format IN (
      'pdf', 'document', 'image', 'sms_archive', 'imessage_archive',
      'whatsapp_export', 'email_archive', 'contacts_file', 'calendar_file',
      'generic_json', 'generic_csv', 'plain_text'
    )
  ),
  CONSTRAINT data_import_status_check CHECK (
    status IN (
      'pending', 'processing', 'completed', 'partially_completed', 'failed',
      'cancelled', 'deleted'
    )
  ),
  PRIMARY KEY (workspace_id, id),
  CONSTRAINT data_import_idempotency_unique
    UNIQUE (workspace_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS data_import_status_created_idx
  ON data_import (workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS data_import_created_by_user_idx
  ON data_import (created_by_user_id, created_at DESC);

DROP TRIGGER IF EXISTS data_import_set_updated_at ON data_import;
CREATE TRIGGER data_import_set_updated_at
BEFORE UPDATE ON data_import
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
