CREATE OR REPLACE VIEW bidirectional_memory_edge AS
SELECT
  e.workspace_id,
  e.id AS edge_id,
  e.from_node_id,
  e.to_node_id,
  e.relation_type_id,
  rt.name AS relation_type,
  e.details,
  e.strength,
  e.confidence,
  CASE
    WHEN e.deleted_at IS NOT NULL THEN 'deleted'
    WHEN e.valid_from IS NOT NULL AND e.valid_from > now() THEN 'scheduled'
    WHEN e.valid_to IS NOT NULL AND e.valid_to <= now() THEN 'expired'
    ELSE 'active'
  END AS status,
  e.created_at,
  e.updated_at
FROM memory_edge e
JOIN relation_type rt ON rt.id = e.relation_type_id

UNION ALL

SELECT
  e.workspace_id,
  e.id AS edge_id,
  e.to_node_id AS from_node_id,
  e.from_node_id AS to_node_id,
  e.relation_type_id,
  rt.inverse_name AS relation_type,
  e.details,
  e.strength,
  e.confidence,
  CASE
    WHEN e.deleted_at IS NOT NULL THEN 'deleted'
    WHEN e.valid_from IS NOT NULL AND e.valid_from > now() THEN 'scheduled'
    WHEN e.valid_to IS NOT NULL AND e.valid_to <= now() THEN 'expired'
    ELSE 'active'
  END AS status,
  e.created_at,
  e.updated_at
FROM memory_edge e
JOIN relation_type rt ON rt.id = e.relation_type_id
WHERE e.from_node_id <> e.to_node_id;
