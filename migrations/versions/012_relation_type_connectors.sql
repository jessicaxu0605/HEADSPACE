-- Store display-ready connector phrases on the relation catalog. Edge reads
-- choose the tense from the edge validity window; names remain stable IDs.

ALTER TABLE relation_type
  ADD COLUMN connector text,
  ADD COLUMN past_connector text,
  ADD COLUMN future_connector text;

UPDATE relation_type AS relation
SET
  connector = connectors.connector,
  past_connector = connectors.past_connector,
  future_connector = connectors.future_connector
FROM (
  VALUES
    ('related_to', 'is connected to', 'was connected to', 'will be connected to'),
    ('associated_with', 'is associated with', 'was associated with', 'will be associated with'),
    ('possibly_same_as', 'is possibly the same as', 'was possibly the same as', 'will possibly be the same as'),
    ('is_a', 'is a', 'was a', 'will be a'),
    ('part_of', 'is part of', 'was part of', 'will be part of'),
    ('located_in', 'is located in', 'was located in', 'will be located in'),
    ('knows', 'knows', 'knew', 'will know'),
    ('met', 'meets', 'met', 'will meet'),
    ('works_with', 'works with', 'worked with', 'will work with'),
    ('friend_of', 'is friends with', 'was friends with', 'will be friends with'),
    ('family_of', 'is a family member of', 'was a family member of', 'will be a family member of'),
    ('parent_of', 'is a parent of', 'was a parent of', 'will be a parent of'),
    ('sibling_of', 'is a sibling of', 'was a sibling of', 'will be a sibling of'),
    ('partner_of', 'is a partner of', 'was a partner of', 'will be a partner of'),
    ('colleague_of', 'is a colleague of', 'was a colleague of', 'will be a colleague of'),
    ('manages', 'manages', 'managed', 'will manage'),
    ('mentors', 'mentors', 'mentored', 'will mentor'),
    ('works_at', 'works at', 'worked at', 'will work at'),
    ('studies_at', 'studies at', 'studied at', 'will study at'),
    ('member_of', 'belongs to', 'belonged to', 'will belong to'),
    ('lives_in', 'lives in', 'lived in', 'will live in'),
    ('originates_from', 'is from', 'was from', 'will be from'),
    ('visited', 'has visited', 'had visited', 'will visit'),
    ('likes', 'likes', 'liked', 'will like'),
    ('dislikes', 'dislikes', 'disliked', 'will dislike'),
    ('prefers', 'prefers', 'preferred', 'will prefer'),
    ('interested_in', 'is interested in', 'was interested in', 'will be interested in'),
    ('wants', 'wants', 'wanted', 'will want'),
    ('avoids', 'avoids', 'avoided', 'will avoid'),
    ('owns', 'owns', 'owned', 'will own'),
    ('has_skill', 'is skilled in', 'was skilled in', 'will be skilled in'),
    ('has_task', 'is responsible for', 'was responsible for', 'will be responsible for'),
    ('for_person', 'is for', 'was for', 'will be for'),
    ('requested', 'requests', 'requested', 'will request'),
    ('about', 'is about', 'was about', 'will be about'),
    ('depends_on', 'depends on', 'depended on', 'will depend on'),
    ('attended', 'attends', 'attended', 'will attend'),
    ('offered_by', 'is offered by', 'was offered by', 'will be offered by'),
    ('will_send', 'will send', 'was going to send', 'will be going to send'),
    ('organized', 'organizes', 'organized', 'will organize'),
    ('occurs_at', 'occurs at', 'occurred at', 'will occur at'),
    ('occurs_on', 'occurs on', 'occurred on', 'will occur on'),
    ('due_on', 'is due on', 'was due on', 'will be due on'),
    ('scheduled_for', 'is scheduled for', 'was scheduled for', 'will be scheduled for'),
    ('born_on', 'is born on', 'was born on', 'will be born on'),
    ('has_birthday', 'has a birthday on', 'had a birthday on', 'will have a birthday on'),
    ('mentions', 'mentions', 'mentioned', 'will mention'),
    ('mentioned_in', 'is mentioned in', 'was mentioned in', 'will be mentioned in'),
    ('created', 'creates', 'created', 'will create')
) AS connectors(name, connector, past_connector, future_connector)
WHERE relation.name = connectors.name;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM relation_type
    WHERE connector IS NULL
       OR past_connector IS NULL
       OR future_connector IS NULL
  ) THEN
    RAISE EXCEPTION 'Every relation type must define present, past, and future connectors';
  END IF;
END
$$;

ALTER TABLE relation_type
  ALTER COLUMN connector SET NOT NULL,
  ALTER COLUMN past_connector SET NOT NULL,
  ALTER COLUMN future_connector SET NOT NULL;

ALTER TABLE relation_type
  ADD CONSTRAINT relation_type_connector_not_blank_check
    CHECK (btrim(connector) <> ''),
  ADD CONSTRAINT relation_type_past_connector_not_blank_check
    CHECK (btrim(past_connector) <> ''),
  ADD CONSTRAINT relation_type_future_connector_not_blank_check
    CHECK (btrim(future_connector) <> '');

COMMENT ON COLUMN relation_type.connector IS
  'Display phrase placed between canonical from/to nodes for an active edge.';
COMMENT ON COLUMN relation_type.past_connector IS
  'Display phrase used when the edge valid_to timestamp has passed.';
COMMENT ON COLUMN relation_type.future_connector IS
  'Display phrase used when the edge valid_from timestamp is in the future.';
