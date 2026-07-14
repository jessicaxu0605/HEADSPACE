CREATE TABLE IF NOT EXISTS memory_evidence_annotation (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  evidence_id uuid NOT NULL,
  "start" integer NOT NULL CHECK ("start" >= 0),
  length integer NOT NULL CHECK (length > 0),
  annotation_text text NOT NULL CHECK (btrim(annotation_text) <> ''),
  created_at timestamptz NOT NULL DEFAULT now(),
  origin_action_id uuid,
  last_modified_action_id uuid,
  PRIMARY KEY (user_id, id),
  FOREIGN KEY (user_id, evidence_id)
    REFERENCES memory_evidence(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, origin_action_id)
    REFERENCES graph_action(user_id, id),
  FOREIGN KEY (user_id, last_modified_action_id)
    REFERENCES graph_action(user_id, id),
  CONSTRAINT memory_evidence_annotation_unique_span UNIQUE (user_id, evidence_id, "start", length)
);

CREATE INDEX IF NOT EXISTS memory_evidence_annotation_evidence_start_idx
  ON memory_evidence_annotation (user_id, evidence_id, "start");
