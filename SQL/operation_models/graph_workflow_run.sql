CREATE TABLE IF NOT EXISTS graph_workflow_run (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  type text NOT NULL CHECK (btrim(type) <> ''),
  status text NOT NULL DEFAULT 'pending',
  actor_type text NOT NULL,
  actor_id uuid,
  payload_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  CONSTRAINT graph_workflow_run_status_check CHECK (
    status IN ('pending', 'applied', 'failed', 'skipped', 'rolled_back')
  ),
  CONSTRAINT graph_workflow_run_actor_type_check CHECK (
    actor_type IN ('user', 'agent', 'ingestion_pipeline', 'system')
  ),
  CONSTRAINT graph_workflow_run_applied_at_check CHECK (
    status <> 'applied' OR applied_at IS NOT NULL
  ),
  PRIMARY KEY (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS graph_workflow_run_status_created_idx
  ON graph_workflow_run (workspace_id, status, created_at DESC);
