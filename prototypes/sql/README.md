# Prototype PostgreSQL schema

These SQL files capture early schema exploration. They are intentionally kept
outside `app/`: they are not the production migration system and are not loaded
by the application.

The folders group experiments by domain:

- `setup`: extensions, users, workspaces, and membership
- `operation_models`: workflow runs and graph actions
- `upload_pipelines`: uploaded data import tracking
- `canonical_graph`: nodes, edges, relation types, identifiers, and graph views
- `type_specific_properties`: profile/detail tables keyed by node
- `source`: sources, evidence, and evidence annotations
- `search`: node and edge search projections

To apply an individual prototype from the repository root:

```sh
DATABASE_URL='postgresql://...' prototypes/sql/apply_sql.sh setup/users.sql
```

The connection string may instead be supplied as `NEON_DATABASE_URL`. These
experiments require PostgreSQL with `pgvector` available. Vector columns have no
fixed dimension because the embedding model has not yet been selected.

When the schema becomes part of the product, replace these scripts with ordered,
versioned migrations (for example, Alembic migrations) in a production database
package. Do not make `app/` import or execute files from this directory.
