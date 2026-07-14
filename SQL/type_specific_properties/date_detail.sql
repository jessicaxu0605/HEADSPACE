CREATE TABLE IF NOT EXISTS date_detail (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  node_id uuid NOT NULL,
  date_value date NOT NULL,
  precision text NOT NULL,
  start_at timestamptz,
  end_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT date_detail_precision_check CHECK (
    precision IN ('day', 'month', 'year', 'datetime', 'range')
  ),
  CONSTRAINT date_detail_range_check CHECK (
    end_at IS NULL OR start_at IS NULL OR end_at > start_at
  ),
  CONSTRAINT date_detail_range_precision_check CHECK (
    precision <> 'range' OR (start_at IS NOT NULL AND end_at IS NOT NULL)
  ),
  PRIMARY KEY (user_id, node_id),
  FOREIGN KEY (user_id, node_id)
    REFERENCES memory_node(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, origin_action_id)
    REFERENCES graph_action(user_id, id),
  FOREIGN KEY (user_id, last_modified_action_id)
    REFERENCES graph_action(user_id, id)
);

DROP TRIGGER IF EXISTS date_detail_set_updated_at ON date_detail;
CREATE TRIGGER date_detail_set_updated_at
BEFORE UPDATE ON date_detail
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
