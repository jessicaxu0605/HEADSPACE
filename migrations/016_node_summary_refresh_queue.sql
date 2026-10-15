-- Summaries from new sources for nodes that already have one. A worker merges
-- them into memory_node.summary outside ingestion (app.workflows.summaries).
--
-- Each node has at most one pending row; later sources append to it, so several
-- ingestions touching the same node are merged in one LLM call. A node can also
-- have one processing row while new summaries collect on a fresh pending row.

CREATE TABLE IF NOT EXISTS node_summary_refresh_queue (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL,
  incoming_summaries text[] NOT NULL CHECK (cardinality(incoming_summaries) > 0),
  status text NOT NULL DEFAULT 'pending' CHECK (
    status IN ('pending', 'processing', 'completed', 'failed')
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS node_summary_refresh_queue_pending_unique_idx
  ON node_summary_refresh_queue (workspace_id, node_id)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS node_summary_refresh_queue_status_idx
  ON node_summary_refresh_queue (status, created_at);

DROP TRIGGER IF EXISTS node_summary_refresh_queue_set_updated_at
  ON node_summary_refresh_queue;
CREATE TRIGGER node_summary_refresh_queue_set_updated_at
BEFORE UPDATE ON node_summary_refresh_queue
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
