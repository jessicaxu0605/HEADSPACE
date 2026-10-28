-- Canonical memory graph persistence used by ingestion Phase 3.
-- PostgreSQL remains the source of truth; search projections are rebuilt from
-- the durable queue below.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS memory_node (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_type text NOT NULL CHECK (
    node_type IN ('person', 'location', 'date', 'task', 'concept')
  ),
  name text NOT NULL CHECK (btrim(name) <> ''),
  summary text,
  type_specific jsonb CHECK (
    type_specific IS NULL OR jsonb_typeof(type_specific) = 'object'
  ),
  importance double precision NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  creation_idempotency_key text,
  merged_into_node_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  CONSTRAINT memory_node_deleted_fields_check CHECK (
    deleted_reason IS NULL OR deleted_at IS NOT NULL
  ),
  CONSTRAINT memory_node_not_merged_into_self_check CHECK (
    merged_into_node_id IS DISTINCT FROM id
  ),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, merged_into_node_id)
    REFERENCES memory_node(workspace_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS memory_node_creation_idempotency_unique_idx
  ON memory_node (workspace_id, creation_idempotency_key)
  WHERE creation_idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_node_type_idx
  ON memory_node (workspace_id, node_type)
  WHERE deleted_at IS NULL AND merged_into_node_id IS NULL;
CREATE INDEX IF NOT EXISTS memory_node_name_idx
  ON memory_node (workspace_id, lower(name))
  WHERE deleted_at IS NULL AND merged_into_node_id IS NULL;

DROP TRIGGER IF EXISTS memory_node_set_updated_at ON memory_node;
CREATE TRIGGER memory_node_set_updated_at
BEFORE UPDATE ON memory_node
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS node_identifier (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL,
  identifier_text text NOT NULL CHECK (btrim(identifier_text) <> ''),
  scope text NOT NULL DEFAULT 'global' CHECK (btrim(scope) <> ''),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS node_identifier_active_unique_idx
  ON node_identifier (workspace_id, node_id, scope, lower(identifier_text))
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS node_identifier_lookup_idx
  ON node_identifier (workspace_id, scope, lower(identifier_text))
  WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS node_identifier_set_updated_at ON node_identifier;
CREATE TRIGGER node_identifier_set_updated_at
BEFORE UPDATE ON node_identifier
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS node_external_id (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  node_id uuid NOT NULL,
  provider text NOT NULL CHECK (btrim(provider) <> ''),
  external_id text NOT NULL CHECK (btrim(external_id) <> ''),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS node_external_id_active_unique_idx
  ON node_external_id (workspace_id, lower(provider), external_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS node_external_id_node_idx
  ON node_external_id (workspace_id, node_id) WHERE deleted_at IS NULL;

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
  CONSTRAINT relation_type_symmetric_inverse_check CHECK (
    NOT is_symmetric OR inverse_name = name
  )
);

DROP TRIGGER IF EXISTS relation_type_set_updated_at ON relation_type;
CREATE TRIGGER relation_type_set_updated_at
BEFORE UPDATE ON relation_type
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO relation_type (name, inverse_name, is_symmetric)
VALUES ('unknown_type', 'unknown_type', true)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS memory_edge (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  from_node_id uuid NOT NULL,
  to_node_id uuid NOT NULL,
  relation_type_id uuid NOT NULL REFERENCES relation_type(id) ON DELETE RESTRICT,
  strength double precision NOT NULL DEFAULT 0 CHECK (strength BETWEEN 0 AND 1),
  confidence double precision NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
  valid_from timestamptz,
  valid_to timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  deleted_reason text,
  CONSTRAINT memory_edge_valid_range_check CHECK (
    valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from
  ),
  CONSTRAINT memory_edge_deleted_fields_check CHECK (
    deleted_reason IS NULL OR deleted_at IS NOT NULL
  ),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, from_node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (workspace_id, to_node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS memory_edge_active_relation_unique_idx
  ON memory_edge (workspace_id, from_node_id, to_node_id, relation_type_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_edge_from_relation_idx
  ON memory_edge (workspace_id, from_node_id, relation_type_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS memory_edge_to_relation_idx
  ON memory_edge (workspace_id, to_node_id, relation_type_id)
  WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS memory_edge_set_updated_at ON memory_edge;
CREATE TRIGGER memory_edge_set_updated_at
BEFORE UPDATE ON memory_edge
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS memory_evidence (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  edge_id uuid NOT NULL,
  source_id uuid NOT NULL,
  raw_content text NOT NULL CHECK (raw_content <> ''),
  confidence double precision NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  strength_boost double precision NOT NULL DEFAULT 0 CHECK (strength_boost BETWEEN 0 AND 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, edge_id)
    REFERENCES memory_edge(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, source_id)
    REFERENCES memory_source(workspace_id, id) ON DELETE RESTRICT,
  CONSTRAINT memory_evidence_source_content_unique
    UNIQUE (workspace_id, edge_id, source_id, raw_content)
);

CREATE INDEX IF NOT EXISTS memory_evidence_edge_idx
  ON memory_evidence (workspace_id, edge_id);
CREATE INDEX IF NOT EXISTS memory_evidence_source_idx
  ON memory_evidence (workspace_id, source_id);

CREATE TABLE IF NOT EXISTS memory_evidence_annotation (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  evidence_id uuid NOT NULL,
  node_id uuid NOT NULL,
  "start" integer NOT NULL CHECK ("start" >= 0),
  length integer NOT NULL CHECK (length > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id),
  FOREIGN KEY (workspace_id, evidence_id)
    REFERENCES memory_evidence(workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, node_id)
    REFERENCES memory_node(workspace_id, id) ON DELETE RESTRICT,
  CONSTRAINT memory_evidence_annotation_unique
    UNIQUE (workspace_id, evidence_id, "start", length, node_id)
);

CREATE INDEX IF NOT EXISTS memory_evidence_annotation_evidence_start_idx
  ON memory_evidence_annotation (workspace_id, evidence_id, "start");

CREATE TABLE IF NOT EXISTS projection_rebuild_queue (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  object_type text NOT NULL CHECK (object_type IN ('node', 'edge')),
  object_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (
    status IN ('pending', 'processing', 'completed', 'failed')
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS projection_rebuild_queue_pending_unique_idx
  ON projection_rebuild_queue (workspace_id, object_type, object_id)
  WHERE status IN ('pending', 'processing');
