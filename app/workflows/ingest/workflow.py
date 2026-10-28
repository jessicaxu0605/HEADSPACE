"""Persistence and orchestration for the source-to-proposed-graph flow."""

from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any
from uuid import UUID

from app.integrations.aws.s3 import write_storage_object
from app.workflows.ingest.enrichment.service import (
    TypeSpecificEnrichmentLLM,
    enrich_type_specific,
)
from app.workflows.ingest.extraction.service import (
    ChunkExtractionLLM,
    extract_proposed_subgraph,
)
from app.workflows.ingest.preparation.service import prepare_source
from app.workflows.ingest.resolution.service import (
    CanonicalGraphResolverStore,
    resolve_proposed_subgraph,
)
from app.workflows.ingest.serialization import to_jsonable
from app.workflows.ingest.models import (
    ExtractionRepairItem,
    IngestionRunRecord,
    MemorySourceInput,
    MemorySourceRecord,
    ProposedSubgraphExtractionResult,
    SourceType,
)
from app.workflows.ingest.sql.postgres import (
    StaleTransactionPlanError,
    _get_or_create_ingestion_run,
    _persist_ingestion_failure,
    _postgres_resolver_store,
    _set_source_preparation_status,
    _update_ingestion_run,
    apply_transaction_plan,
    create_memory_source,
    get_ingestion_run,
)


logger = logging.getLogger(__name__)


class IngestionExecutionError(RuntimeError):
    """An ingestion run failed after its durable run record was created."""

    def __init__(self, run: IngestionRunRecord, cause: Exception) -> None:
        self.run = run
        self.cause = cause
        super().__init__(f"Ingestion run {run.id} failed: {cause}")




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
    enrichment_llm: TypeSpecificEnrichmentLLM | None = None,
    resolver_store: CanonicalGraphResolverStore | None = None,
) -> IngestionRunRecord:
    """Persist a source, extract its graph, and atomically apply graph changes."""

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
    if run.status in {"completed", "processing"}:
        return run

    _set_source_preparation_status(source, "processing")
    run = _update_ingestion_run(run.id, status="processing")
    failure_phase = "extraction"
    try:
        extraction_result: ProposedSubgraphExtractionResult = (
            await extract_proposed_subgraph(
                prepare_source(source),
                llm,
                # The Groq adapter loads the active catalog per ingest; reuse it
                # for endpoint validation. Test doubles may not carry one.
                relation_definitions=getattr(llm, "relation_definitions", None),
            )
        )
        if enrichment_llm is not None:
            await _enrich_type_specific_or_record(
                source, extraction_result, enrichment_llm
            )
        failure_phase = "resolve"
        transaction_plan = await resolve_proposed_subgraph(
            workspace_id=workspace_id,
            source_id=source.id,
            proposed_graph=extraction_result.proposed_graph,
            store=(
                resolver_store
                if resolver_store is not None
                else _postgres_resolver_store()
            ),
        )
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        _set_source_preparation_status(source, "failed")
        failed_run = _update_ingestion_run(run.id, status="failed", error=error)
        _persist_ingestion_failure(
            run_id=run.id,
            workspace_id=workspace_id,
            source_id=source.id,
            phase=failure_phase,
            error=error,
        )
        raise IngestionExecutionError(failed_run, exc) from exc

    result = to_jsonable(extraction_result)
    result["transaction_plan"] = to_jsonable(transaction_plan)
    try:
        return apply_transaction_plan(
            run_id=run.id,
            plan=transaction_plan,
            result=result,
        )
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        _set_source_preparation_status(source, "failed")
        failed_run = _update_ingestion_run(run.id, status="failed", error=error)
        _persist_ingestion_failure(
            run_id=run.id,
            workspace_id=workspace_id,
            source_id=source.id,
            phase="apply",
            error=error,
            plan=transaction_plan,
            result=result,
        )
        raise IngestionExecutionError(failed_run, exc) from exc


async def _enrich_type_specific_or_record(
    source: MemorySourceRecord,
    extraction_result: ProposedSubgraphExtractionResult,
    enrichment_llm: TypeSpecificEnrichmentLLM,
) -> None:
    """Fill type-specific fields; a failure leaves them unfilled, not the run."""

    try:
        await enrich_type_specific(
            prepare_source(source),
            extraction_result.proposed_graph,
            enrichment_llm,
            extraction_context=extraction_result.updated_extraction_context,
        )
    except Exception as exc:
        logger.warning(
            "Type-specific enrichment failed for source %s", source.id, exc_info=True
        )
        extraction_result.repair_items.append(
            ExtractionRepairItem(
                message=f"Type-specific enrichment failed: {exc}",
                item_type="type_specific_enrichment_failed",
                details={"severity": "low", "error_type": type(exc).__name__},
            )
        )
