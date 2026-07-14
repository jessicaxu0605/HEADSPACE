CREATE TABLE IF NOT EXISTS person_profile (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  node_id uuid NOT NULL,
  display_name text,
  legal_name text,
  email text,
  phone_number text,
  instagram text,
  pronouns text,
  relationship_context text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  PRIMARY KEY (user_id, node_id),
  FOREIGN KEY (user_id, node_id)
    REFERENCES memory_node(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, origin_action_id)
    REFERENCES graph_action(user_id, id),
  FOREIGN KEY (user_id, last_modified_action_id)
    REFERENCES graph_action(user_id, id)
);

DROP TRIGGER IF EXISTS person_profile_set_updated_at ON person_profile;
CREATE TRIGGER person_profile_set_updated_at
BEFORE UPDATE ON person_profile
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
