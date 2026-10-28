# Ingest Handoff

Status: October 14, 2026

The ingest prototype implements Phase 1 (extract a proposed subgraph), Phase 2
(resolve-or-create a canonical transaction plan), and Phase 3 (atomically apply
that plan to the canonical PostgreSQL graph).

## Current end-to-end flow

`POST /api/v1/ingestions` currently does the following:

1. Builds a source idempotency key from source type, workspace ID, and client
   request ID.
2. Writes the raw source to private object storage.
3. Creates or reuses a `memory_source` and its single `ingestion_run`.
4. Prepares and chunks the source.
5. Calls the Groq extraction adapter once per chunk and aggregates the returned
   chunk-local graph records.
5a. Runs a second Groq pass over the source to fill type-specific node fields
   (see "Type-specific enrichment").
6. Runs create-or-resolve against a `CanonicalGraphResolverStore`.
7. Applies canonical nodes, identifiers, external IDs, edges, evidence, evidence
   annotations, archives, and projection rebuild requests in one transaction.
8. Marks the source ready and persists the extraction result and applied plan in
   `ingestion_run.result_json` in that same transaction.
9. Prints the proposed graph and transaction plan as formatted JSON in the API
   process console for prototype testing.

The completed result retains the existing extraction shape and adds the plan:

```json
{
  "proposed_graph": {"nodes": [], "edges": []},
  "proposed_graph_handoff": {"nodes": [], "edges": []},
  "updated_extraction_context": {},
  "repair_items": [],
  "transaction_plan": {
    "workspace_id": "...",
    "source_id": "...",
    "node_bindings": [],
    "node_actions": [],
    "edge_actions": [],
    "repair_items": []
  }
}
```

## Phase 1: proposed-subgraph extraction

Implementation: `app/workflows/ingest/extraction/service.py`

- Iterates asynchronously over prepared chunks with one-chunk lookahead.
- Supplies previous and next boundary text only for sentence/reference context.
- Sends a compact rolling graph handoff and extraction context to the model.
- Each model call returns records introduced or enriched by the current chunk,
  not a complete graph snapshot.
- Repeated node IDs merge names, external IDs, summaries, and type-specific
  fields. Node type cannot change for an existing proposed ID.
- Repeated edge IDs append unique evidence. Endpoints and relation type cannot
  change for an existing proposed edge ID.
- Accumulated limits are 200 nodes and 500 edges. Hitting either limit fails the
  job; continuation with a canonical handoff is not implemented.

Validation happens before candidate graph state, repair items, context, or
handoff are committed. It validates IDs, names, endpoint existence, handoff
consistency, verbatim evidence, evidence scores, annotation ranges/references, and
relation endpoint types (the issue lists the labels valid for that node-type pair).
Endpoint mismatches still present after the last repair attempt are accepted and
left to resolution rather than failing the job.
Domain-invalid output receives up to two repair attempts. Graph-limit failures
are not retried.

The fixed node-type catalog is `person`, `organization`, `location`, `event`,
`document`, `date`, `task`, and `concept`.

### Groq adapter

Implementation: `app/workflows/ingest/extraction/adapters/groq_chunk.py`

`GroqChunkExtractionLLM` owns extraction/repair prompts, strict structured-output
schema, dataclass and UUID serialization, malformed-response repair, SDK transport
retries, and parsing into `ChunkExtractionOutput`. Its relation enum and catalog
prompt are built from active, extractable `relation_type` rows loaded from
PostgreSQL for each ingestion; relation definitions are not duplicated in code.

## Type-specific enrichment

Implementation: `app/workflows/ingest/enrichment/service.py`,
`app/workflows/ingest/enrichment/adapters/groq.py`, field catalog in
`app/workflows/ingest/enrichment/fields.py`.

Runs between extraction and resolution. Each chunk is sent once with the
person/location/date/task nodes it mentions; the result fills
`ProposedNode.type_specific`. Failures are logged and recorded as a
`type_specific_enrichment_failed` review item without failing the run. Fields
are stored in `memory_node.type_specific` (jsonb) until per-type tables exist.

## Phase 2: create-or-resolve

Implementation: `app/workflows/ingest/resolution/service.py`

Resolution is read/plan-only. It binds all nodes first, then resolves edges using
canonical node IDs. It returns a `ResolutionTransactionPlan`; it does not mutate
the canonical graph.

### Candidate matching

`SimpleCanonicalGraphResolverStore` is the concrete prototype implementation. It
operates on a supplied in-memory canonical graph snapshot and deliberately does
not use search vectors.

Candidate precedence is:

1. Exact provider and external ID.
2. Exact normalized identifier.
3. Exact normalized title for a compatible node type.
4. Fuzzy lexical comparison with `difflib.SequenceMatcher` across proposed names,
   canonical titles, and canonical identifiers.

The store emits fuzzy candidates at a score of 0.50 or higher. The resolution
policy then:

- resolves fuzzy matches at 0.92 or higher;
- treats the top result as ambiguous when the next result is within 0.08;
- retains matches at 0.65 or higher as possible duplicate candidates;
- conservatively creates identifiers for names sufficiently different from the
  canonical title;
- promotes a more user-facing full name over an acronym, handle, or email title,
  retaining the old title as an identifier;
- normalizes person email names into `email` external IDs and can match a readable
  name to a normalized email local-part;
- uses already-resolved graph neighbors as additional evidence for otherwise
  fuzzy candidates, and emits a review item for weak possible duplicates;
- merges a second high-confidence email-identity candidate into the selected
  canonical node, moving identifiers, external IDs, annotations, edges, and
  evidence without hard-deleting the old node;
- treats incompatible exact external-ID matches as ambiguous rather than
  creating a conflicting external ID.
- stores person `email`/`phone_number`/`instagram` only as external IDs, never
  in `type_specific`;
- matches date and location nodes on a type-specific identity key (date value and
  precision, or Google Place ID) and blocks candidates whose key differs;
- fills missing type-specific fields on resolved nodes
  (`FillCanonicalNodeTypeSpecific`) and reports differing values as
  `conflicting_type_specific` review items.
- fills a resolved node's empty summary (`FillCanonicalNodeSummary`) and queues
  a different incoming summary for the summary refresh worker
  (`QueueCanonicalNodeSummaryRefresh`, `app/workflows/summaries/`).

These thresholds are explicitly marked for later tuning.

### Planned node changes

The transaction plan can contain:

- canonical node creation with a stable source/proposed-node idempotency key;
- proposed-to-canonical node bindings;
- identifier creation;
- external-ID creation;
- ambiguity repair items instead of unsafe resolution.

### Planned edge changes

After node binding, the resolver:

- canonicalizes fixed inverse relation labels by swapping endpoints;
- canonicalizes symmetric edge endpoint ordering;
- repairs endpoint-incompatible relations by reversing them when the reverse
  direction fits, or by switching to the single type-restricted relation that
  fits the endpoint types (`repaired_relation_endpoints`, low severity);
- otherwise falls unsupported or endpoint-incompatible relations back to
  `related_to` and emits durable review items instead of extending the relation
  catalog;
- binds evidence annotations from proposed IDs to canonical UUIDs;
- updates an existing edge with identical endpoints and relation type;
- upgrades an `unknown_type` edge when a more specific relation arrives;
- otherwise plans a new canonical edge;
- skips edges whose endpoints or annotated nodes could not be resolved;
- emits repair items for weak evidence that conflicts with an exclusive active
  relation;
- archives conflicting exclusive edges when replacement evidence meets the
  configured confidence threshold.

Edge confidence and strength are derived from evidence using the design formulas:

```text
confidence = 1 - PRODUCT(1 - evidence.confidence)
strength contribution = evidence.strength_boost * evidence.confidence
strength = 1 - PRODUCT(1 - strength contribution)
```

New evidence combines with existing edge scores using the same diminishing-return
formula.

## Orchestration and API

Implementation:

- `app/workflows/ingest/workflow.py`
- `app/main.py`
- `app/workflows/ingest/models.py`
- `app/workflows/ingest/serialization.py`

`ingest_content()` accepts an injectable `CanonicalGraphResolverStore`. The API
uses `PostgresCanonicalGraphResolverStore`, which loads the active workspace graph
and delegates lexical scoring to `SimpleCanonicalGraphResolverStore`. Tests can
still inject in-memory snapshots.

Extraction and resolution share the same failure boundary. If either raises, the
source is marked `failed`, the run stores a small error payload, and the API returns
the failed run as a 502 response. Completed and currently-processing idempotent
requests return the existing run.

The API currently exposes:

- `POST /api/v1/ingestions`
- `GET /api/v1/ingestions/{run_id}`
- `GET /health`

Workspace routing is still temporary and uses the hardcoded prototype workspace
created by `ensure_temporary_prototype_workspace()`.

## Persistence currently available

Migrations:

- `migrations/001_ingestion_api.sql`
- `migrations/002_canonical_graph.sql`

The production migration currently creates:

- `users`
- `workspaces`
- `memory_source`
- `ingestion_run`

Raw source objects use S3 through `app/integrations/aws/s3.py`. The database connection is
configured for PostgreSQL/Neon through
`app/db/postgres.py`.

The second migration adds canonical nodes, identifiers, external IDs, relation
types, edges, evidence, annotations, and the durable projection rebuild queue.
It was applied to the configured Neon database on October 14, 2026.

`migrations/005_fixed_relation_types_and_review_items.sql` seeds the fixed V1
relation catalog and adds `graph_review_item`. Extraction and resolution repair
items are written there atomically with a successful ingest. Proposed
`unknown_type` or otherwise unsupported edges are stored as `related_to` and
flagged for review.

`migrations/006_persist_failed_ingestion_reviews.sql` adds the `apply` review
phase and backfills failed ingestion runs. New extraction, resolution, and apply
failures are persisted in a separate transaction after the graph transaction
rolls back, so retrying or failing an ingest does not erase its review record.

`migrations/008_database_relation_catalog.sql` makes PostgreSQL the relation
catalog source of truth with `is_active` and `is_extractable`, deactivates the old
set without breaking historical edges, and installs the current canonical set.
See `Design Docs/Relation Types.md` for the complete current vocabulary,
direction and endpoint rules, extraction availability, and update procedure.

`migrations/009_relation_type_hierarchy.sql` adds an optional self-referencing
`parent_name` and seeds a conservative specificity hierarchy. No automatic edge
upgrade job runs during ingest yet; the hierarchy is metadata for a future
confidence-gated job.

`migrations/010_memory_edge_details.sql` adds nullable free-text
`memory_edge.details`. Extraction emits `details` per edge alongside
`relation_type`; the type remains the edge identity and details carry meaning
for retrieval, display, and LLM context. Details are merged with
`merge_edge_details` across chunks, on apply, and when node merges collapse
duplicate edges. The future edge projection worker should include details in
`embedding_text`.

`migrations/012_relation_type_connectors.sql` stores present, past, and future
human-readable connector phrases for every relation type. Edge queries select
the future form before `valid_from`, the past form at or after `valid_to`, and
the present form otherwise.

## Important limitations and next work

1. Add a worker that consumes `projection_rebuild_queue` and materializes search
   projections.
2. Replace lexical snapshot scoring with indexed/vector candidate retrieval.
3. Decide whether ingestion status should distinguish `planned` from `applied`.
4. Replace temporary workspace routing with authenticated workspace selection.
5. Remove or gate console printing once structured observability is available.

Medium-similarity `related_to`/`unknown_type` edges described in the design notes
are not automatically generated yet. Candidate nodes are preserved on node-create
actions so that policy can be added without changing candidate retrieval.

## Tests and verification

Relevant tests:

- `tests/test_extract_proposed_subgraph.py`
- `tests/test_groq_chunk_extraction.py`
- `tests/test_resolve_proposed_subgraph.py`
- `tests/test_ingest_workflow.py`
- `tests/test_ingest_api.py`

Run:

```bash
venv/bin/python -m compileall -q app tests
venv/bin/python -m unittest discover -v
git diff --check
```

Current result: 43 tests pass; 3 live Groq integration tests are skipped unless
`RUN_GROQ_INTEGRATION_TEST=1` is set.

## Working-tree caution

The repository currently has uncommitted changes, including unrelated user work
in environment and frontend files. Do not reset or discard the worktree. The
current relevant ingest changes are also uncommitted at the time of this handoff.

Recent history:

```text
191429f workflow
f05a775 api
```
