from __future__ import annotations

from app.db import get_connection
from app.workflows.extract_proposed_subgraph import extract_proposed_subgraph
from app.workflows.types import MemorySourceInput, MemorySourceRecord
from psycopg.types.json import Jsonb


def create_memory_source(source_input: MemorySourceInput) -> MemorySourceRecord:
    with get_connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO memory_source (
                    workspace_id,
                    source_type,
                    storage_object_key,
                    source_metadata,
                    source_idempotency_key
                )
                VALUES (
                    %(workspace_id)s,
                    %(source_type)s,
                    %(storage_object_key)s,
                    %(source_metadata)s,
                    %(source_idempotency_key)s
                )
                ON CONFLICT (workspace_id, source_idempotency_key)
                DO UPDATE SET source_idempotency_key = EXCLUDED.source_idempotency_key
                RETURNING
                    workspace_id,
                    id,
                    source_type,
                    storage_object_key,
                    source_metadata,
                    source_idempotency_key
                """,
                {
                    "workspace_id": source_input.workspace_id,
                    "source_type": source_input.source_type,
                    "storage_object_key": source_input.storage_object_key,
                    "source_metadata": Jsonb(source_input.source_metadata),
                    "source_idempotency_key": source_input.source_idempotency_key,
                },
            ).fetchone()

            return MemorySourceRecord(**row)


# returns a transaction plan
def resolve_proposed_subgraph():
    pass

# applies a transaction plan to the database
def apply_transaction_plan():
    pass
