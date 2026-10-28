-- Add first-class V1 node types needed for organizational and source context.

ALTER TABLE memory_node
  DROP CONSTRAINT IF EXISTS memory_node_node_type_check;
ALTER TABLE memory_node
  ADD CONSTRAINT memory_node_node_type_check CHECK (
    node_type IN (
      'person',
      'organization',
      'location',
      'event',
      'document',
      'date',
      'task',
      'concept'
    )
  );

UPDATE relation_type
SET allowed_to_types = '{organization,concept,location}', updated_at = now()
WHERE name IN ('works_at', 'studies_at');
