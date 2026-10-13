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
in PostgreSQL, calls Groq for chunk extraction, and persists the resulting proposed
graph. Canonical graph application is not wired in yet.

Add the existing Neon connection string and Groq key to `.env.local`:

```bash
cp .env.example .env.local
venv/bin/python -m app.migrate
```

If your network blocks outbound PostgreSQL connections on port 5432, open the
Neon Console's SQL Editor for the target branch/database instead:

1. Paste and run `migrations/001_ingestion_api.sql`.
2. Then run the migration tracking statements below:

```sql
CREATE TABLE IF NOT EXISTS schema_migration (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migration (version)
VALUES ('001_ingestion_api.sql')
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

The completed response includes `result.proposed_graph`. Fetch a persisted run
later with `GET /api/v1/ingestions/{run_id}`. Interactive API documentation is at
`http://localhost:8000/docs`.

> **Temporary prototype identity:** Until authentication and workspace selection
> are implemented, every request is assigned to the hardcoded workspace UUID
> `00000000-0000-0000-0000-000000000002`. This value is not a real tenant ID and
> must be removed before multi-user or production deployment.
