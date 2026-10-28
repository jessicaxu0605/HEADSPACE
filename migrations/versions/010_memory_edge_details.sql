-- Edge details carry relationship meaning that the relation type does not.
-- relation_type remains the edge's structured identity (deduplication,
-- exclusivity, temporal handling, endpoint constraints, structured queries);
-- details are free text for retrieval, display, and LLM context, e.g.
-- likes(Sarah, Matcha): 'especially strawberry matcha lattes; usually orders it iced'.

ALTER TABLE memory_edge
  ADD COLUMN details text;

ALTER TABLE memory_edge
  ADD CONSTRAINT memory_edge_details_not_blank_check
  CHECK (details IS NULL OR btrim(details) <> '');

COMMENT ON COLUMN memory_edge.details IS
  'Free-text qualifier of the relationship, accumulated across evidence. Never part of edge identity.';
