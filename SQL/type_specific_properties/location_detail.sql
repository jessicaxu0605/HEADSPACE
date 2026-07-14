CREATE TABLE IF NOT EXISTS location_detail (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  node_id uuid NOT NULL,
  address text,
  latitude double precision CHECK (latitude BETWEEN -90 AND 90),
  longitude double precision CHECK (longitude BETWEEN -180 AND 180),
  google_place_id text,
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

CREATE UNIQUE INDEX IF NOT EXISTS location_detail_google_place_unique_idx
  ON location_detail (user_id, google_place_id) WHERE google_place_id IS NOT NULL;

DROP TRIGGER IF EXISTS location_detail_set_updated_at ON location_detail;
CREATE TRIGGER location_detail_set_updated_at
BEFORE UPDATE ON location_detail
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
