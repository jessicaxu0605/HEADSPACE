-- Failed ingestion transactions roll back graph changes, but their failure still
-- needs to survive as a reviewable record.

ALTER TABLE graph_review_item
  DROP CONSTRAINT IF EXISTS graph_review_item_phase_check;
ALTER TABLE graph_review_item
  ADD CONSTRAINT graph_review_item_phase_check
  CHECK (phase IN ('extraction', 'resolve', 'apply'));

INSERT INTO graph_review_item (
  workspace_id,
  ingestion_run_id,
  source_id,
  review_key,
  phase,
  item_type,
  severity,
  message,
  payload_json
)
SELECT
  workspace_id,
  id,
  source_id,
  source_id::text || ':apply:historical-failure',
  'apply',
  'ingestion_failure',
  'high',
  COALESCE(error_json->>'message', 'Ingestion failed and requires review.'),
  jsonb_build_object('error', error_json)
FROM ingestion_run
WHERE status = 'failed'
  AND error_json IS NOT NULL
ON CONFLICT (workspace_id, review_key) DO NOTHING;
