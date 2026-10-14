-- Record which canonical nodes and edges each ingestion run created or changed.
-- A node or edge is usually touched by many runs over its lifetime, so this is a
-- join table rather than a column on memory_node / memory_edge. Rows are written
-- in the same transaction that applies the run's plan.
--
-- change_kind:
--   created  - the run inserted the row
--   updated  - the run changed an existing row (evidence, details, identifiers,
--              title, endpoints, or a merge target)
--   archived - the run soft-deleted the edge
--   merged   - the run merged this node into another node

CREATE TABLE IF NOT EXISTS ingestion_run_graph_change (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES ingestion_run(id) ON DELETE CASCADE,
  object_type text NOT NULL CHECK (object_type IN ('node', 'edge')),
  object_id uuid NOT NULL,
  change_kind text NOT NULL CHECK (
    change_kind IN ('created', 'updated', 'archived', 'merged')
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, object_type, object_id)
);

CREATE INDEX IF NOT EXISTS ingestion_run_graph_change_object_idx
  ON ingestion_run_graph_change (workspace_id, object_type, object_id);

-- Best-effort backfill for runs completed before this table existed. Node
-- creation keys are prefixed with the source id, so created nodes are exact.
-- Edges are attributed through their evidence; an edge counts as created by the
-- run whose source supplied its earliest evidence. Archives and merges from
-- earlier runs are not recoverable.

INSERT INTO ingestion_run_graph_change (
  workspace_id, run_id, object_type, object_id, change_kind
)
SELECT n.workspace_id, run.id, 'node', n.id, 'created'
FROM ingestion_run AS run
JOIN memory_node AS n
  ON n.workspace_id = run.workspace_id
 AND n.creation_idempotency_key LIKE run.source_id::text || ':%'
WHERE run.status = 'completed'
ON CONFLICT DO NOTHING;

INSERT INTO ingestion_run_graph_change (
  workspace_id, run_id, object_type, object_id, change_kind
)
SELECT DISTINCT ON (run.id, ev.edge_id)
  run.workspace_id,
  run.id,
  'edge',
  ev.edge_id,
  CASE
    WHEN first_ev.source_id = run.source_id THEN 'created'
    ELSE 'updated'
  END
FROM ingestion_run AS run
JOIN memory_evidence AS ev
  ON ev.workspace_id = run.workspace_id
 AND ev.source_id = run.source_id
JOIN LATERAL (
  SELECT e2.source_id
  FROM memory_evidence AS e2
  WHERE e2.workspace_id = ev.workspace_id
    AND e2.edge_id = ev.edge_id
  ORDER BY e2.created_at, e2.id
  LIMIT 1
) AS first_ev ON true
WHERE run.status = 'completed'
ORDER BY run.id, ev.edge_id
ON CONFLICT DO NOTHING;

-- Endpoints of backfilled edges that the run did not create were still touched.
INSERT INTO ingestion_run_graph_change (
  workspace_id, run_id, object_type, object_id, change_kind
)
SELECT DISTINCT c.workspace_id, c.run_id, 'node', endpoint.node_id, 'updated'
FROM ingestion_run_graph_change AS c
JOIN memory_edge AS e
  ON e.workspace_id = c.workspace_id AND e.id = c.object_id
CROSS JOIN LATERAL (VALUES (e.from_node_id), (e.to_node_id)) AS endpoint(node_id)
WHERE c.object_type = 'edge'
ON CONFLICT DO NOTHING;
