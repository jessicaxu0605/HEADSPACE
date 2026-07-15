CREATE TABLE IF NOT EXISTS person_profile (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
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
  PRIMARY KEY (workspace_id, node_id),
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, origin_action_id)
    REFERENCES graph_action(workspace_id, id),
  FOREIGN KEY (workspace_id, last_modified_action_id)
    REFERENCES graph_action(workspace_id, id)
);

DROP TRIGGER IF EXISTS person_profile_set_updated_at ON person_profile;
CREATE TRIGGER person_profile_set_updated_at
BEFORE UPDATE ON person_profile
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
