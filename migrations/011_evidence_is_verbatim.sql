-- Evidence raw_content is requested as a verbatim span of the source, but
-- extraction no longer rejects paraphrased or context-derived evidence. This
-- flag records which kind each row is. Earlier rows were validated as verbatim.

ALTER TABLE memory_evidence
  ADD COLUMN IF NOT EXISTS is_verbatim boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN memory_evidence.is_verbatim IS
  'True when raw_content appears in the source chunk (ignoring whitespace); false when the model paraphrased it or inferred it from context.';
