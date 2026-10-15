# Headspace

The repository separates deployable application code from design exploration:

```text
app/                 Python application package
  main.py             API entrypoint and upload/status/retry endpoints
  db.py               Database engine and session setup
  models.py           Application-owned PostgreSQL models
  storage.py          Local or object-storage helpers
  graph/
    queries/           Graph read contracts and result models
  persistence/
    postgres/          PostgreSQL implementations of application stores
  workflows/
    ingest/             Ingestion orchestration, extraction, and resolution steps
  jobs/
    queue.py           Redis/RQ configuration and enqueue helpers
    tasks.py           Background job entrypoints
    stages.py          Pipeline stage orchestration
prototypes/sql/       Disposable SQL schema experiments
Design Docs/          Product and architecture notes (local/ignored)
```

Prototype SQL is reference material, not part of the runtime application. Once a schema is adopted, express it as versioned production migrations rather than having the app execute prototype `CREATE` scripts.

## Ingestion API

The MVP API accepts manual entries, email text/JSON, and calendar event text/JSON. It stores the complete source in a private S3 bucket, persists the source and ingestion run in PostgreSQL, calls Groq for chunk extraction, resolves against the current workspace graph, and applies the canonical graph transaction atomically.

```bash
cp .env.example .env.local
venv/bin/python -m app.migrate
```

Start the API:

```bash
venv/bin/uvicorn app.main:app --reload
```

The API opens an asynchronous PostgreSQL connection pool at startup and closes it at shutdown. `DATABASE_POOL_MIN_SIZE` and `DATABASE_POOL_MAX_SIZE` default to `1`and `5`; adjust them to match the database connection budget.

Submit content from the frontend or curl. Generate `client_request_id` once and reuse it when retrying the same submission:

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

The completed response includes the extracted `result.proposed_graph` and the applied create-or-resolve output in `result.transaction_plan`. Fetch a persisted run later with `GET /api/v1/ingestions/{run_id}`. Interactive API documentation is at `http://localhost:8000/docs`.

Query canonical nodes with the same exact-then-fuzzy identity matcher used by ingestion. Omit `node_type` to search every supported type, or include it to narrow the results. Aliases and external IDs are optional identity signals:

```bash
curl -X POST http://localhost:8000/api/v1/nodes/query \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Sara Chen",
    "aliases": ["Sarah"],
    "limit": 5
  }'
```

The response contains ranked candidates with their match kind (`external_id`, `identifier`, `normalized_title`, or `fuzzy`), score, and scoring reasons. The current prototype performs indexed exact matching and PostgreSQL trigram fuzzy matching in one bounded query until the documented search projections are available.

Fetch a node's active graph neighborhood with a bounded hop depth:

```bash
curl 'http://localhost:8000/api/v1/nodes/00000000-0000-0000-0000-000000000123/graph?depth=2'
```

The response includes the starting node, every active canonical node reachable within `depth` relationships, each node's shortest `distance`, and every unique active edge whose endpoints are both in the result. Traversal treats edges as bidirectional, tracks visited node IDs to stop cycles, and preserves each edge's canonical direction in the response. PostgreSQL performs the bounded traversal with a recursive CTE and indexed edge frontiers, returning only the reached subgraph. Depth is limited to 10 for the prototype.

> **Default development workspace:** Until authentication supplies workspace
> context, every request uses workspace UUID
> `00000000-0000-0000-0000-000000000002`. Migration
> `003_default_development_workspace.sql` provisions it once; API requests do not
> create or update it.

## Local embeddings

The embedding provider lives in `app/embeddings/` and uses Ollama's native `/api/embed` endpoint. The default is `nomic-embed-text` (768 dimensions), with the model's `search_document:` and `search_query:` prefixes applied by the adapter. Returned vectors are validated and normalized for cosine-distance search.

Start Ollama and download the default model once:

```bash
brew services start ollama
ollama pull nomic-embed-text
```

Provider URL, model, dimensions, and prefixes can be changed with the`OLLAMA_BASE_URL` and `EMBEDDING_*` settings in `.env.example`. Model and dimension must be changed together; the `embedding` columns of `node_search_projection` and `edge_search_projection` are `vector(768)`, so a different width also needs a migration.

### Search projections

`app/search/node_projection.py` builds two projections per canonical node, as described in Design V1 "Build projections": `identity` (name, aliases, node type, abstracted external-ID providers, stable type-specific fields, and the strongest `is_a`/`part_of`/`located_in` edges) and `profile` (summary, changeable type-specific facts, and the highest strength-times-confidence edges as sentences). Each is stored in `node_search_projection` with its search text, embedding text, vector, and a source hash so unchanged text is not re-embedded.

`app/search/edge_projection.py` builds one projection per canonical edge: the edge as a sentence using the tense-appropriate catalog connector, the relation type and endpoint types with the first sentence of the catalog description (skipped for `related_to`), the edge `details`, its validity window, and up to three evidence quotes ranked by confidence with annotated mentions replaced by node names. It is stored in `edge_search_projection`.

Ingestion queues affected nodes and edges in `projection_rebuild_queue`. To process the queue:

```bash
python -m app.search.rebuild_projections
```

Vector search queries are not built yet.

### Node summaries

Ingestion sets a node's summary only when it has none. When a resolved node already has a different summary, the incoming one is queued in `node_summary_refresh_queue` (one pending row per node, so several sources are merged together). To merge queued summaries with Groq:

```bash
python -m app.workflows.summaries.refresh_summaries
```

Updated nodes are queued for a projection rebuild, so run `rebuild_projections` afterwards.
