CREATE TABLE IF NOT EXISTS graph_action (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  previous_action_id uuid,
  next_action_id uuid,
  run_id uuid,
  type text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  actor_type text NOT NULL,
  actor_id uuid,
  payload_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  CONSTRAINT graph_action_type_check CHECK (
    type IN (
      'create_node_action', 'update_node_action',
      'create_edge_action', 'update_edge_action',
      'create_identifier', 'delete_identifier', 'restore_snapshot'
    )
  ),
  CONSTRAINT graph_action_status_check CHECK (
    status IN ('pending', 'applied', 'failed', 'skipped', 'rolled_back')
  ),
  CONSTRAINT graph_action_actor_type_check CHECK (
    actor_type IN ('user', 'agent', 'ingestion_pipeline', 'system')
  ),
  CONSTRAINT graph_action_not_self_linked CHECK (
    previous_action_id IS DISTINCT FROM id AND next_action_id IS DISTINCT FROM id
  ),
  CONSTRAINT graph_action_applied_at_check CHECK (
    status <> 'applied' OR applied_at IS NOT NULL
  ),
  PRIMARY KEY (user_id, id),
  FOREIGN KEY (user_id, previous_action_id)
    REFERENCES graph_action(user_id, id),
  FOREIGN KEY (user_id, next_action_id)
    REFERENCES graph_action(user_id, id),
  FOREIGN KEY (user_id, run_id)
    REFERENCES graph_workflow_run(user_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS graph_action_previous_unique_idx
  ON graph_action (user_id, previous_action_id) WHERE previous_action_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS graph_action_next_unique_idx
  ON graph_action (user_id, next_action_id) WHERE next_action_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS graph_action_run_created_idx
  ON graph_action (user_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS graph_action_status_created_idx
  ON graph_action (user_id, status, created_at DESC);
