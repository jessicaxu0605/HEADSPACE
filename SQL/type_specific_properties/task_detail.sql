CREATE TABLE IF NOT EXISTS task_detail (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  node_id uuid NOT NULL,
  due_at timestamptz,
  completed_at timestamptz,
  urgency text,
  task_status text NOT NULL DEFAULT 'todo',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT task_detail_urgency_check CHECK (
    urgency IS NULL OR urgency IN ('low', 'medium', 'high')
  ),
  CONSTRAINT task_detail_status_check CHECK (
    task_status IN ('todo', 'in_progress', 'blocked', 'done', 'archived')
  ),
  CONSTRAINT task_detail_completion_check CHECK (
    task_status <> 'done' OR completed_at IS NOT NULL
  ),
  PRIMARY KEY (user_id, node_id),
  FOREIGN KEY (user_id, node_id)
    REFERENCES memory_node(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, origin_action_id)
    REFERENCES graph_action(user_id, id),
  FOREIGN KEY (user_id, last_modified_action_id)
    REFERENCES graph_action(user_id, id)
);

CREATE INDEX IF NOT EXISTS task_detail_status_due_idx ON task_detail (user_id, task_status, due_at);

DROP TRIGGER IF EXISTS task_detail_set_updated_at ON task_detail;
CREATE TRIGGER task_detail_set_updated_at
BEFORE UPDATE ON task_detail
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
