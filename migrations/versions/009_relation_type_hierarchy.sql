-- Add a conservative relation hierarchy for future evidence-based edge
-- specialization. parent_name identifies the immediately broader canonical
-- relation; NULL marks a hierarchy root.

ALTER TABLE relation_type
  ADD COLUMN parent_name text;

ALTER TABLE relation_type
  ADD CONSTRAINT relation_type_parent_name_fk
  FOREIGN KEY (parent_name)
  REFERENCES relation_type(name)
  ON UPDATE CASCADE
  ON DELETE RESTRICT;

ALTER TABLE relation_type
  ADD CONSTRAINT relation_type_parent_not_self_check
  CHECK (parent_name IS NULL OR parent_name <> name);

COMMENT ON COLUMN relation_type.parent_name IS
  'Immediately broader canonical relation. A future high-confidence job may replace a parent edge with a descendant edge for the same node pair.';

-- All ordinary relations first specialize the generic fallback. Identity
-- matching is a separate system concern, so possibly_same_as remains a root.
UPDATE relation_type
SET parent_name = CASE
  WHEN name IN ('related_to', 'possibly_same_as') THEN NULL
  ELSE 'related_to'
END
WHERE is_active;

-- Only encode subtype relationships that are safe enough to drive automatic
-- upgrades. Avoid plausible but non-entailing links such as prefers -> likes,
-- avoids -> dislikes, or works_at -> member_of.
UPDATE relation_type
SET parent_name = 'knows'
WHERE is_active
  AND name IN (
    'friend_of',
    'family_of',
    'partner_of',
    'colleague_of',
    'manages',
    'mentors'
  );

UPDATE relation_type
SET parent_name = 'family_of'
WHERE is_active
  AND name IN ('parent_of', 'sibling_of');

UPDATE relation_type
SET parent_name = 'located_in'
WHERE is_active
  AND name = 'occurs_at';

CREATE INDEX relation_type_parent_name_idx
  ON relation_type (parent_name)
  WHERE parent_name IS NOT NULL;
