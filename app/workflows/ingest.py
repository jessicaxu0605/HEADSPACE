"""Persistence and orchestration for the source-to-proposed-graph flow."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.db import get_connection
from app.serialization import to_jsonable
from app.storage import write_storage_object
from app.workflows.extract_proposed_subgraph import (
    ChunkExtractionLLM,
    extract_proposed_subgraph,
)
from app.workflows.prepare_source import prepare_source
from app.workflows.types import (
    IngestionRunRecord,
    MemorySourceInput,
    MemorySourceRecord,
    ProposedSubgraphExtractionResult,
    SourceType,
)


class IngestionExecutionError(RuntimeError):
    """An ingestion run failed after its durable run record was created."""

    def __init__(self, run: IngestionRunRecord, cause: Exception) -> None:
        self.run = run
        self.cause = cause
        super().__init__(f"Ingestion run {run.id} failed: {cause}")


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

    if row is None:
        raise RuntimeError("PostgreSQL did not return the persisted memory source.")
    return MemorySourceRecord(**row)


def get_ingestion_run(run_id: UUID) -> IngestionRunRecord | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                workspace_id,
                source_id,
                status,
                result_json AS result,
                error_json AS error,
                created_at,
                updated_at
            FROM ingestion_run
            WHERE id = %s
            """,
            (run_id,),
        ).fetchone()
    return IngestionRunRecord(**row) if row is not None else None


def _get_or_create_ingestion_run(source: MemorySourceRecord) -> IngestionRunRecord:
    with get_connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO ingestion_run (workspace_id, source_id)
                VALUES (%s, %s)
                ON CONFLICT (workspace_id, source_id)
                DO UPDATE SET source_id = EXCLUDED.source_id
                RETURNING
                    id,
                    workspace_id,
                    source_id,
                    status,
                    result_json AS result,
                    error_json AS error,
                    created_at,
                    updated_at
                """,
                (source.workspace_id, source.id),
            ).fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not return the ingestion run.")
    return IngestionRunRecord(**row)


def _set_source_preparation_status(
    source: MemorySourceRecord,
    status: str,
) -> None:
    with get_connection() as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE memory_source
                SET preparation_status = %s, updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (status, source.workspace_id, source.id),
            )


def _update_ingestion_run(
    run_id: UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> IngestionRunRecord:
    with get_connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """
                UPDATE ingestion_run
                SET
                    status = %(status)s,
                    result_json = %(result)s,
                    error_json = %(error)s,
                    updated_at = now()
                WHERE id = %(run_id)s
                RETURNING
                    id,
                    workspace_id,
                    source_id,
                    status,
                    result_json AS result,
                    error_json AS error,
                    created_at,
                    updated_at
                """,
                {
                    "run_id": run_id,
                    "status": status,
                    "result": Jsonb(result) if result is not None else None,
                    "error": Jsonb(error) if error is not None else None,
                },
            ).fetchone()
    if row is None:
        raise LookupError(f"Ingestion run {run_id} does not exist.")
    return IngestionRunRecord(**row)


def build_source_idempotency_key(
    *,
    source_type: SourceType,
    workspace_id: UUID,
    client_request_id: str,
) -> str:
    return f"{source_type}:{workspace_id}:{client_request_id}"


def build_storage_object_key(
    *,
    workspace_id: UUID,
    source_idempotency_key: str,
) -> str:
    digest = sha256(source_idempotency_key.encode("utf-8")).hexdigest()
    return f"{workspace_id}/sources/{digest}.bin"


async def ingest_content(
    *,
    workspace_id: UUID,
    source_type: SourceType,
    content: str,
    source_metadata: dict[str, Any],
    client_request_id: str,
    llm: ChunkExtractionLLM,
) -> IngestionRunRecord:
    """Persist a source, extract its proposed graph, and persist the result."""

    idempotency_key = build_source_idempotency_key(
        source_type=source_type,
        workspace_id=workspace_id,
        client_request_id=client_request_id,
    )
    storage_object_key = build_storage_object_key(
        workspace_id=workspace_id,
        source_idempotency_key=idempotency_key,
    )
    write_storage_object(storage_object_key, content.encode("utf-8"))

    source = create_memory_source(
        MemorySourceInput(
            workspace_id=workspace_id,
            source_type=source_type,
            storage_object_key=storage_object_key,
            source_metadata=source_metadata,
            source_idempotency_key=idempotency_key,
        )
    )
    run = _get_or_create_ingestion_run(source)
    return run


    if run.status in {"completed", "processing"}:
        return run

    _set_source_preparation_status(source, "processing")
    run = _update_ingestion_run(run.id, status="processing")
    try:
        extraction_result: ProposedSubgraphExtractionResult = (
            await extract_proposed_subgraph(prepare_source(source), llm)
        )
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        _set_source_preparation_status(source, "failed")
        failed_run = _update_ingestion_run(run.id, status="failed", error=error)
        raise IngestionExecutionError(failed_run, exc) from exc

    _set_source_preparation_status(source, "ready")
    return _update_ingestion_run(
        run.id,
        status="completed",
        result=to_jsonable(extraction_result),
    )


# Phase 3: applies a transaction plan to the database.
def apply_transaction_plan() -> None:
    pass
