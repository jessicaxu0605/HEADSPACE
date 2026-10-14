# Headspace

The repository separates deployable application code from design exploration:

```text
app/                 Python application package
  main.py             API entrypoint and upload/status/retry endpoints
  db.py               Database engine and session setup
  models.py           Application-owned PostgreSQL models
  storage.py          Local or object-storage helpers
  jobs/
    queue.py           Redis/RQ configuration and enqueue helpers
    tasks.py           Background job entrypoints
    stages.py          Pipeline stage orchestration
prototypes/sql/       Disposable SQL schema experiments
Design Docs/          Product and architecture notes (local/ignored)
```

Prototype SQL is reference material, not part of the runtime application. Once
a schema is adopted, express it as versioned production migrations rather than
having the app execute prototype `CREATE` scripts.

## Ingestion API

The MVP API accepts manual entries, email text/JSON, and calendar event text/JSON.
It stores the complete source in a private S3 bucket, persists the source and run
in PostgreSQL, calls Groq for chunk extraction, resolves against the current
workspace graph, and applies the canonical graph transaction atomically.

Add the existing Neon connection string and Groq key to `.env.local`:

```bash
cp .env.example .env.local
venv/bin/python -m app.migrate
```

If your network blocks outbound PostgreSQL connections on port 5432, open the
Neon Console's SQL Editor for the target branch/database instead:

1. Paste and run each unapplied file in `migrations/` in filename order.
2. Then run the migration tracking statements below:

```sql
CREATE TABLE IF NOT EXISTS schema_migration (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migration (version)
VALUES ('001_ingestion_api.sql'), ('002_canonical_graph.sql')
ON CONFLICT (version) DO NOTHING;
```

This creates the schema without requiring a local connection. The running API
still needs a network path to Neon's PostgreSQL endpoint; use a deployment network
that permits port 5432 if the local network continues to time out.

Start the API:

```bash
venv/bin/uvicorn app.main:app --reload
```

Submit content from the frontend or curl. Generate `client_request_id` once and
reuse it when retrying the same submission:

```bash
curl -X POST http://localhost:8000/api/v1/ingestions \
  -H 'Content-Type: application/json' \
  -d '{
    "source_type": "manual_entry",
    "content": "Alice knows Bob.",
    "source_metadata": {"author": "Jessica"},
    "client_request_id": "demo-001"
  }'
```

The completed response includes the extracted `result.proposed_graph` and the
applied create-or-resolve output in `result.transaction_plan`. Fetch a persisted
run later with
`GET /api/v1/ingestions/{run_id}`. Interactive API documentation is at
`http://localhost:8000/docs`.

Query canonical nodes with the same exact-then-fuzzy identity matcher used by
ingestion. The node type is required because lexical matches are type-compatible;
aliases and external IDs are optional additional identity signals:

```bash
curl -X POST http://localhost:8000/api/v1/nodes/query \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Sara Chen",
    "node_type": "person",
    "aliases": ["Sarah"],
    "limit": 5
  }'
```

The response contains ranked candidates with their match kind (`external_id`,
`identifier`, `normalized_title`, or `fuzzy`), score, and scoring reasons. The
current prototype reads the active workspace graph directly and uses lexical
similarity until the documented search projections are available.

> **Temporary prototype identity:** Until authentication and workspace selection
> are implemented, every request is assigned to the hardcoded workspace UUID
> `00000000-0000-0000-0000-000000000002`. This value is not a real tenant ID and
> must be removed before multi-user or production deployment.
