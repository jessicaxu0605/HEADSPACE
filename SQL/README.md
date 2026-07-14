# Headspace PostgreSQL schema

The schema files are organized by the design overview sections:

- `setup`: extensions and shared helper functions
- `setup/users.sql`: minimal user records
- `operation_models`: workflow runs and graph actions
- `canonical_graph`: nodes, edges, relation types, identifiers, and graph views
- `type_specific_properties`: profile/detail tables keyed by node
- `source`: sources, evidence, and evidence annotations
- `search`: node and edge search projections

Apply them in dependency order from the repository root:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/setup/extensions_and_helpers.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/setup/users.sql

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/operation_models/graph_workflow_run.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/operation_models/graph_action.sql

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/canonical_graph/relation_type.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/canonical_graph/memory_node.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/canonical_graph/memory_edge.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/canonical_graph/node_identifier.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/canonical_graph/node_external_identifier.sql

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/type_specific_properties/person_profile.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/type_specific_properties/location_detail.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/type_specific_properties/date_detail.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/type_specific_properties/task_detail.sql

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/source/memory_source.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/source/memory_evidence.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/source/memory_evidence_annotation.sql

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/search/node_search_projection.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/search/edge_search_projection.sql

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f SQL/canonical_graph/bidirectional_memory_edge.sql
```

You can also apply one script at a time with:

```sh
DATABASE_URL='postgresql://...' SQL/apply_sql.sh SQL/setup/users.sql
```

For local Neon credentials, put the connection string in `.env.local`:

```sh
NEON_DATABASE_URL='postgresql://...'
```

Then load it before running the script:

```sh
set -a
source .env.local
set +a
SQL/apply_sql.sh SQL/setup/users.sql
```

The schema requires PostgreSQL with the `pgvector` extension available. Vector
columns intentionally have no fixed dimension because the embedding model has
not been selected. Add a dimension (for example, `vector(1536)`) before creating
HNSW or IVFFlat vector indexes.

Closed value sets are stored as `text` columns with `CHECK` constraints instead
of PostgreSQL enum types. `graph_workflow_run.type` is a plain text value
because the design does not define a closed value set for workflow run types.
`node_search_projection` and `edge_search_projection` include `embedding_model`
so projections produced by different models cannot collide.

Tenant-owned tables carry `user_id` and use composite primary keys and foreign
keys such as `(user_id, id)` and `(user_id, node_id)`. This prevents rows owned
by one user from referencing nodes, edges, evidence, or projections owned by
another user. `relation_type` is intentionally global and does not carry
`user_id`.
