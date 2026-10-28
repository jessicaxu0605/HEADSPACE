-- PostgreSQL is the source of truth for the active relation vocabulary. Old
-- relation rows remain addressable by historical edges but are not offered to
-- extraction or accepted for new edges.

ALTER TABLE relation_type
  ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
ALTER TABLE relation_type
  ADD COLUMN IF NOT EXISTS is_extractable boolean NOT NULL DEFAULT true;

UPDATE relation_type
SET is_active = false, is_extractable = false, updated_at = now();

INSERT INTO relation_type (
  name, inverse_name, is_symmetric, is_exclusive, can_change_over_time,
  allowed_from_types, allowed_to_types, description, is_active, is_extractable
)
VALUES
  ('related_to', 'related_to', true, false, false, '{}', '{}', 'Fallback: the two nodes have a meaningful relationship that no more specific relation type captures. Prefer a specific type whenever one fits.', true, true),
  ('possibly_same_as', 'possibly_same_as', true, false, false, '{}', '{}', 'System-only: the resolver found a medium-confidence identity match. Input to the merge job; never produced by extraction.', true, false),

  ('is_a', 'has_instance', false, false, false, '{}', '{}', 'The subject is an instance or subtype of the object.', true, true),
  ('part_of', 'has_part', false, false, false, '{}', '{}', 'The subject is a component or member-part of the object.', true, true),
  ('located_in', 'contains', false, false, true, '{}', '{location}', 'The subject is physically located within the object.', true, true),

  ('knows', 'knows', true, false, false, '{person}', '{person}', 'The two people know each other. Use when no more specific relationship is stated.', true, true),
  ('friend_of', 'friend_of', true, false, true, '{person}', '{person}', 'The two people are friends.', true, true),
  ('family_of', 'family_of', true, false, false, '{person}', '{person}', 'The two people are family, where the specific relationship is unknown.', true, true),
  ('parent_of', 'child_of', false, false, false, '{person}', '{person}', 'The subject is a parent of the object.', true, true),
  ('sibling_of', 'sibling_of', true, false, false, '{person}', '{person}', 'The two people are siblings.', true, true),
  ('partner_of', 'partner_of', true, false, true, '{person}', '{person}', 'The two people are in a romantic relationship or married.', true, true),
  ('colleague_of', 'colleague_of', true, false, true, '{person}', '{person}', 'The two people work together.', true, true),
  ('manages', 'managed_by', false, false, true, '{person}', '{person}', 'The subject is the manager or supervisor of the object.', true, true),
  ('mentors', 'mentored_by', false, false, true, '{person}', '{person}', 'The subject mentors or advises the object.', true, true),

  ('works_at', 'employs', false, false, true, '{person}', '{organization,location}', 'The person is employed by or works at the organization or place.', true, true),
  ('studies_at', 'has_student', false, false, true, '{person}', '{organization,location}', 'The person studies at the institution.', true, true),
  ('member_of', 'has_member', false, false, true, '{person,organization}', '{organization}', 'The subject belongs to the group, club, team or organization.', true, true),
  ('lives_in', 'residence_of', false, true, true, '{person}', '{location}', 'The person currently lives in the location.', true, true),
  ('originates_from', 'origin_of', false, false, false, '{person}', '{location}', 'The person is from the location.', true, true),
  ('visited', 'visited_by', false, false, false, '{person}', '{location}', 'The person has been to the location.', true, true),

  ('likes', 'liked_by', false, false, true, '{}', '{}', 'The subject likes or enjoys the object.', true, true),
  ('dislikes', 'disliked_by', false, false, true, '{}', '{}', 'The subject dislikes the object.', true, true),
  ('prefers', 'preferred_by', false, false, true, '{}', '{}', 'The subject chooses the object over alternatives. Use likes for plain enjoyment.', true, true),
  ('interested_in', 'interest_of', false, false, true, '{}', '{}', 'The subject is curious about or follows the topic or activity.', true, true),
  ('wants', 'wanted_by', false, false, true, '{}', '{}', 'The subject wants to have or do the object.', true, true),
  ('avoids', 'avoided_by', false, false, true, '{}', '{}', 'The subject must or chooses to avoid the object. Stronger than dislikes.', true, true),
  ('owns', 'owned_by', false, false, true, '{}', '{}', 'The subject owns or possesses the object.', true, true),
  ('has_skill', 'skill_of', false, false, true, '{person}', '{concept}', 'The person is able to do, or is proficient in, the object.', true, true),

  ('has_task', 'task_of', false, false, false, '{person}', '{task}', 'The person is responsible for doing the task.', true, true),
  ('requested', 'requested_by', false, false, false, '{person}', '{task}', 'The person asked for the task to be done.', true, true),
  ('about', 'subject_of', false, false, false, '{task,event,document}', '{}', 'The task, event or document is about, or done for, the object.', true, true),
  ('depends_on', 'required_by', false, false, false, '{}', '{}', 'The subject cannot proceed or be completed without the object.', true, true),

  ('attended', 'attended_by', false, false, false, '{person}', '{event}', 'The person attended or took part in the event.', true, true),
  ('organized', 'organized_by', false, false, false, '{person,organization}', '{event}', 'The subject organized or hosted the event.', true, true),
  ('occurs_at', 'venue_of', false, false, true, '{event}', '{location}', 'The event takes or took place at the location.', true, true),

  ('occurs_on', 'occurrence_date_of', false, true, true, '{event}', '{date}', 'The event happens or happened on the date.', true, true),
  ('due_on', 'due_date_of', false, true, true, '{task}', '{date}', 'The task must be completed by the date.', true, true),
  ('scheduled_for', 'scheduled_date_of', false, true, true, '{task}', '{date}', 'The task is planned to be done on the date, distinct from its deadline.', true, true),
  ('born_on', 'birth_date_of', false, true, false, '{person}', '{date}', 'The person''s birthday. The date node may have month-day precision only.', true, true),

  ('mentions', 'mentioned_in', false, false, false, '{document}', '{}', 'The document or note refers to the object without being primarily about it.', true, true),
  ('created', 'created_by', false, false, false, '{person,organization}', '{}', 'The subject wrote, made or authored the object.', true, true)
ON CONFLICT (name) DO UPDATE SET
  inverse_name = EXCLUDED.inverse_name,
  is_symmetric = EXCLUDED.is_symmetric,
  is_exclusive = EXCLUDED.is_exclusive,
  can_change_over_time = EXCLUDED.can_change_over_time,
  allowed_from_types = EXCLUDED.allowed_from_types,
  allowed_to_types = EXCLUDED.allowed_to_types,
  description = EXCLUDED.description,
  is_active = EXCLUDED.is_active,
  is_extractable = EXCLUDED.is_extractable,
  updated_at = now();

CREATE INDEX IF NOT EXISTS relation_type_active_name_idx
  ON relation_type (name) WHERE is_active;
