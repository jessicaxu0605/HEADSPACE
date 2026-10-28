-- Constrain ingestion to the V1 relation catalog and persist skipped graph
-- changes for manual review.

INSERT INTO relation_type (
  name,
  inverse_name,
  is_symmetric,
  is_exclusive,
  can_change_over_time,
  allowed_from_types,
  allowed_to_types,
  description
)
VALUES
  ('likes', 'liked_by', false, false, false, '{}', '{}', 'The subject likes the object.'),
  ('dislikes', 'disliked_by', false, false, false, '{}', '{}', 'The subject dislikes the object.'),
  ('prefers', 'preferred_by', false, false, false, '{}', '{}', 'The subject prefers the object.'),
  ('interested_in', 'interest_of', false, false, false, '{}', '{}', 'The subject is interested in the object.'),
  ('knows', 'known_by', false, false, false, '{person}', '{person}', 'The subject knows the object.'),
  ('met', 'met', true, false, false, '{person}', '{person}', 'The two subjects met.'),
  ('works_with', 'works_with', true, false, false, '{person}', '{person}', 'The two subjects work together.'),
  ('works_at', 'employs', false, false, true, '{person}', '{concept,location}', 'The subject works at the organization or place.'),
  ('lives_in', 'residence_of', false, true, true, '{person}', '{location}', 'The subject lives in the location.'),
  ('located_in', 'contains', false, false, true, '{}', '{location}', 'The subject is physically located in the object.'),
  ('attended', 'attended_by', false, false, false, '{}', '{}', 'The subject attended the object.'),
  ('has_birthday', 'birthday_of', false, true, false, '{person}', '{date}', 'The subject has the given birthday.'),
  ('mentioned_in', 'mentions', false, false, false, '{}', '{}', 'The subject is mentioned in the object.'),
  ('depends_on', 'required_by', false, false, false, '{}', '{}', 'The subject depends on the object.'),
  ('is_a', 'has_instance', false, false, false, '{}', '{}', 'The subject is an instance or subtype of the object.'),
  ('for_person', 'has_task', false, false, false, '{task}', '{person}', 'The task is for the person.'),
  ('studies_at', 'has_student', false, false, true, '{person}', '{concept,location}', 'The person studies at the institution.'),
  ('offered_by', 'offers', false, false, false, '{}', '{}', 'The subject is offered by the object.'),
  ('will_send', 'will_receive_from', false, false, false, '{}', '{}', 'The subject intends to send the object.'),
  ('related_to', 'related_to', true, false, false, '{}', '{}', 'The two subjects have a meaningful general relationship.'),
  ('associated_with', 'associated_with', true, false, false, '{}', '{}', 'The two subjects are explicitly associated.')
ON CONFLICT (name) DO UPDATE SET
  inverse_name = EXCLUDED.inverse_name,
  is_symmetric = EXCLUDED.is_symmetric,
  is_exclusive = EXCLUDED.is_exclusive,
  can_change_over_time = EXCLUDED.can_change_over_time,
  allowed_from_types = EXCLUDED.allowed_from_types,
  allowed_to_types = EXCLUDED.allowed_to_types,
  description = EXCLUDED.description,
  updated_at = now();

CREATE TABLE IF NOT EXISTS graph_review_item (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  ingestion_run_id uuid NOT NULL REFERENCES ingestion_run(id) ON DELETE CASCADE,
  source_id uuid NOT NULL,
  review_key text NOT NULL CHECK (btrim(review_key) <> ''),
  phase text NOT NULL CHECK (phase IN ('extraction', 'resolve')),
  item_type text NOT NULL CHECK (btrim(item_type) <> ''),
  severity text NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'resolved', 'dismissed')),
  message text NOT NULL CHECK (btrim(message) <> ''),
  proposed_node_id text,
  proposed_edge_id text,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload_json) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, source_id)
    REFERENCES memory_source(workspace_id, id) ON DELETE CASCADE,
  CONSTRAINT graph_review_item_key_unique UNIQUE (workspace_id, review_key),
  CONSTRAINT graph_review_item_resolved_status_check CHECK (
    (status = 'pending' AND resolved_at IS NULL)
    OR (status IN ('resolved', 'dismissed') AND resolved_at IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS graph_review_item_pending_idx
  ON graph_review_item (workspace_id, created_at DESC)
  WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS graph_review_item_source_idx
  ON graph_review_item (workspace_id, source_id);

DROP TRIGGER IF EXISTS graph_review_item_set_updated_at ON graph_review_item;
CREATE TRIGGER graph_review_item_set_updated_at
BEFORE UPDATE ON graph_review_item
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
