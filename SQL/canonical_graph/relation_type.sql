CREATE TABLE IF NOT EXISTS relation_type (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
  inverse_name text NOT NULL CHECK (btrim(inverse_name) <> ''),
  is_symmetric boolean NOT NULL DEFAULT false,
  is_exclusive boolean NOT NULL DEFAULT false,
  can_change_over_time boolean NOT NULL DEFAULT false,
  allowed_from_types text[] NOT NULL DEFAULT '{}',
  allowed_to_types text[] NOT NULL DEFAULT '{}',
  description text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  CONSTRAINT relation_type_symmetric_inverse_check CHECK (
    NOT is_symmetric OR inverse_name = name
  )
);

CREATE INDEX IF NOT EXISTS relation_type_inverse_name_idx ON relation_type (inverse_name);

DROP TRIGGER IF EXISTS relation_type_set_updated_at ON relation_type;
CREATE TRIGGER relation_type_set_updated_at
BEFORE UPDATE ON relation_type
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
