"""Persistence and orchestration for the source-to-proposed-graph flow."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from app.graph.edge_helpers import combine_edge_details, combine_probabilities
from app.persistence.postgres.connection import get_connection
from app.serialization import to_jsonable
from app.storage import write_storage_object
from app.workflows.ingest.enrich_type_specific import (
    TypeSpecificEnrichmentLLM,
    enrich_type_specific,
)
from app.workflows.ingest.extract_proposed_subgraph import (
    ChunkExtractionLLM,
    extract_proposed_subgraph,
)
from app.workflows.ingest.prepare_source import prepare_source
from app.workflows.ingest.resolve_proposed_subgraph import (
    ArchiveCanonicalEdge,
    CanonicalGraphResolverStore,
    CreateCanonicalNode,
    CreateNodeExternalId,
    CreateNodeIdentifier,
    FillCanonicalNodeSummary,
    FillCanonicalNodeTypeSpecific,
    QueueCanonicalNodeSummaryRefresh,
    ResolutionTransactionPlan,
    UpdateCanonicalNodeTitle,
    UpsertCanonicalEdge,
    resolve_proposed_subgraph,
)
from app.workflows.ingest.types import (
    ExtractionRepairItem,
    IngestionRunRecord,
    MemorySourceInput,
    MemorySourceRecord,
    ProposedSubgraphExtractionResult,
    SourceType,
)
from app.workflows.summaries.refresh_summaries import enqueue_summary_refresh


logger = logging.getLogger(__name__)


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


def get_ingestion_run(
    *, workspace_id: UUID, run_id: UUID
) -> IngestionRunRecord | None:
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
            WHERE workspace_id = %s AND id = %s
            """,
            (workspace_id, run_id),
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


class StaleTransactionPlanError(RuntimeError):
    """The canonical graph changed in a way that makes a plan unsafe to apply."""


def _postgres_resolver_store() -> CanonicalGraphResolverStore:
    # Keep the resolution policy module persistence-agnostic while making live
    # PostgreSQL reads the default for production ingestion.
    from app.embeddings import OllamaEmbeddingEngine
    from app.persistence.postgres.canonical_graph import (
        PostgresCanonicalGraphResolverStore,
    )

    return PostgresCanonicalGraphResolverStore(embedder=OllamaEmbeddingEngine())


@dataclass
class _RunGraphChanges:
    """Nodes and edges whose change kind is more specific than 'updated'."""

    created_nodes: set[UUID] = field(default_factory=set)
    created_edges: set[UUID] = field(default_factory=set)
    archived_edges: set[UUID] = field(default_factory=set)


def _record_run_graph_changes(
    conn: Any,
    *,
    run_id: UUID,
    workspace_id: UUID,
    affected_nodes: set[UUID],
    affected_edges: set[UUID],
    changes: _RunGraphChanges,
) -> None:
    rows: list[tuple[str, UUID, str]] = []
    for node_id in affected_nodes:
        if node_id in changes.created_nodes:
            kind = "created"
        else:
            kind = "updated"
        rows.append(("node", node_id, kind))
    for edge_id in affected_edges:
        # An edge created and then superseded in one run reads as archived.
        if edge_id in changes.archived_edges:
            kind = "archived"
        elif edge_id in changes.created_edges:
            kind = "created"
        else:
            kind = "updated"
        rows.append(("edge", edge_id, kind))
    for object_type, object_id, change_kind in rows:
        conn.execute(
            """
            INSERT INTO ingestion_run_graph_change (
                workspace_id, run_id, object_type, object_id, change_kind
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (run_id, object_type, object_id)
            DO UPDATE SET change_kind = EXCLUDED.change_kind
            """,
            (workspace_id, run_id, object_type, object_id, change_kind),
        )


def _map_node_id(node_id: UUID, node_ids: dict[UUID, UUID]) -> UUID:
    return node_ids.get(node_id, node_id)


def _assert_active_node(conn: Any, *, workspace_id: UUID, node_id: UUID) -> None:
    row = conn.execute(
        """
        SELECT 1
        FROM memory_node
        WHERE workspace_id = %s
          AND id = %s
          AND deleted_at IS NULL
          AND merged_into_node_id IS NULL
        FOR SHARE
        """,
        (workspace_id, node_id),
    ).fetchone()
    if row is None:
        raise StaleTransactionPlanError(
            f"Canonical node {node_id} is no longer active."
        )


def _enqueue_projection_rebuild(
    conn: Any,
    *,
    workspace_id: UUID,
    object_type: str,
    object_id: UUID,
) -> None:
    conn.execute(
        """
        INSERT INTO projection_rebuild_queue (
            workspace_id, object_type, object_id
        )
        VALUES (%s, %s, %s)
        ON CONFLICT (workspace_id, object_type, object_id)
          WHERE status IN ('pending', 'processing')
        DO NOTHING
        """,
        (workspace_id, object_type, object_id),
    )


def _apply_node_actions(
    conn: Any,
    plan: ResolutionTransactionPlan,
    changes: _RunGraphChanges,
) -> tuple[dict[UUID, UUID], set[UUID]]:
    node_ids: dict[UUID, UUID] = {}
    affected_node_ids: set[UUID] = set()

    for action in plan.node_actions:
        if not isinstance(action, CreateCanonicalNode):
            continue
        row = conn.execute(
            """
            INSERT INTO memory_node (
                workspace_id,
                id,
                node_type,
                name,
                summary,
                type_specific,
                creation_idempotency_key
            )
            VALUES (
                %(workspace_id)s,
                %(node_id)s,
                %(node_type)s,
                %(name)s,
                %(summary)s,
                %(type_specific)s,
                %(idempotency_key)s
            )
            ON CONFLICT (workspace_id, creation_idempotency_key)
              WHERE creation_idempotency_key IS NOT NULL
            DO NOTHING
            RETURNING id, node_type
            """,
            {
                "workspace_id": plan.workspace_id,
                "node_id": action.node_id,
                "node_type": action.node_type,
                "name": action.title,
                "summary": action.summary,
                "type_specific": (
                    Jsonb(action.type_specific)
                    if action.type_specific is not None
                    else None
                ),
                "idempotency_key": action.idempotency_key,
            },
        ).fetchone()
        if row is not None:
            changes.created_nodes.add(row["id"])
        else:
            row = conn.execute(
                """
                SELECT id, node_type
                FROM memory_node
                WHERE workspace_id = %s
                  AND creation_idempotency_key = %s
                  AND deleted_at IS NULL
                FOR UPDATE
                """,
                (plan.workspace_id, action.idempotency_key),
            ).fetchone()
        if row is None or row["node_type"] != action.node_type:
            raise StaleTransactionPlanError(
                f"Node idempotency key {action.idempotency_key!r} is not reusable."
            )
        node_ids[action.node_id] = row["id"]
        affected_node_ids.add(row["id"])

    for action in plan.node_actions:
        if isinstance(action, CreateCanonicalNode):
            continue
        node_id = _map_node_id(action.node_id, node_ids)
        _assert_active_node(
            conn,
            workspace_id=plan.workspace_id,
            node_id=node_id,
        )
        if isinstance(action, UpdateCanonicalNodeTitle):
            conn.execute(
                """
                UPDATE memory_node
                SET name = %s, updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (action.title, plan.workspace_id, node_id),
            )
        elif isinstance(action, FillCanonicalNodeSummary):
            conn.execute(
                """
                UPDATE memory_node
                SET summary = %s, updated_at = now()
                WHERE workspace_id = %s
                  AND id = %s
                  AND (summary IS NULL OR btrim(summary) = '')
                """,
                (action.summary, plan.workspace_id, node_id),
            )
        elif isinstance(action, QueueCanonicalNodeSummaryRefresh):
            enqueue_summary_refresh(
                conn,
                workspace_id=plan.workspace_id,
                node_id=node_id,
                incoming_summaries=[action.incoming_summary],
            )
        elif isinstance(action, FillCanonicalNodeTypeSpecific):
            # Right side wins in jsonb ||, so existing values are kept.
            conn.execute(
                """
                UPDATE memory_node
                SET type_specific = %s || COALESCE(type_specific, '{}'::jsonb),
                    updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (Jsonb(action.type_specific), plan.workspace_id, node_id),
            )
        elif isinstance(action, CreateNodeIdentifier):
            conn.execute(
                """
                INSERT INTO node_identifier (
                    workspace_id, node_id, identifier_text
                )
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (plan.workspace_id, node_id, action.identifier_text),
            )
        elif isinstance(action, CreateNodeExternalId):
            conn.execute(
                """
                INSERT INTO node_external_id (
                    workspace_id, node_id, provider, external_id
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    plan.workspace_id,
                    node_id,
                    action.provider,
                    action.external_id,
                ),
            )
            owner = conn.execute(
                """
                SELECT node_id
                FROM node_external_id
                WHERE workspace_id = %s
                  AND lower(provider) = lower(%s)
                  AND external_id = %s
                  AND deleted_at IS NULL
                FOR UPDATE
                """,
                (plan.workspace_id, action.provider, action.external_id),
            ).fetchone()
            if owner is None or owner["node_id"] != node_id:
                raise StaleTransactionPlanError(
                    f"External ID {action.provider}:{action.external_id} "
                    "now belongs to another canonical node."
                )
        else:  # pragma: no cover - the closed action union prevents this.
            raise TypeError(f"Unsupported node action: {type(action).__name__}")
        affected_node_ids.add(node_id)

    return node_ids, affected_node_ids


def _get_relation_type(
    conn: Any,
    relation_name: str,
    *,
    allow_inactive: bool = False,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, name, is_exclusive, can_change_over_time
        FROM relation_type
        WHERE name = %s
          AND (%s OR is_active)
        """,
        (relation_name, allow_inactive),
    ).fetchone()
    if row is None:
        raise StaleTransactionPlanError(
            f"Relation type {relation_name!r} is not active in the database catalog."
        )
    return row


def _insert_evidence(
    conn: Any,
    *,
    plan: ResolutionTransactionPlan,
    action: UpsertCanonicalEdge,
    edge_id: UUID,
    node_ids: dict[UUID, UUID],
) -> tuple[list[float], list[float]]:
    confidence_values: list[float] = []
    strength_values: list[float] = []
    for evidence in action.evidence:
        if evidence.source_id != plan.source_id:
            raise ValueError("Edge evidence does not belong to the transaction source.")
        for annotation in evidence.annotations:
            if annotation.start + annotation.length > len(evidence.raw_content):
                raise ValueError("Evidence annotation falls outside its raw content.")
        row = conn.execute(
            """
            INSERT INTO memory_evidence (
                workspace_id,
                edge_id,
                source_id,
                raw_content,
                confidence,
                strength_boost,
                is_verbatim
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, edge_id, source_id, raw_content)
            DO NOTHING
            RETURNING id
            """,
            (
                plan.workspace_id,
                edge_id,
                evidence.source_id,
                evidence.raw_content,
                evidence.confidence,
                evidence.strength_boost or 0.0,
                evidence.is_verbatim,
            ),
        ).fetchone()
        inserted = row is not None
        if row is None:
            row = conn.execute(
                """
                SELECT id
                FROM memory_evidence
                WHERE workspace_id = %s
                  AND edge_id = %s
                  AND source_id = %s
                  AND raw_content = %s
                """,
                (
                    plan.workspace_id,
                    edge_id,
                    evidence.source_id,
                    evidence.raw_content,
                ),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by the unique constraint.
            raise RuntimeError("PostgreSQL did not return persisted evidence.")
        for annotation in evidence.annotations:
            conn.execute(
                """
                INSERT INTO memory_evidence_annotation (
                    workspace_id, evidence_id, node_id, "start", length
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    plan.workspace_id,
                    row["id"],
                    _map_node_id(annotation.node_id, node_ids),
                    annotation.start,
                    annotation.length,
                ),
            )
        if inserted:
            confidence_values.append(evidence.confidence)
            strength_values.append((evidence.strength_boost or 0.0) * evidence.confidence)
    return confidence_values, strength_values


def _apply_edge_actions(
    conn: Any,
    plan: ResolutionTransactionPlan,
    node_ids: dict[UUID, UUID],
    changes: _RunGraphChanges,
) -> tuple[set[UUID], set[UUID]]:
    affected_node_ids: set[UUID] = set()
    affected_edge_ids: set[UUID] = set()

    for action in plan.edge_actions:
        if not isinstance(action, ArchiveCanonicalEdge):
            continue
        row = conn.execute(
            """
            UPDATE memory_edge
            SET deleted_at = now(), deleted_reason = %s
            WHERE workspace_id = %s AND id = %s AND deleted_at IS NULL
            RETURNING id, from_node_id, to_node_id
            """,
            (action.reason, plan.workspace_id, action.edge_id),
        ).fetchone()
        if row is not None:
            affected_edge_ids.add(row["id"])
            changes.archived_edges.add(row["id"])
            affected_node_ids.update((row["from_node_id"], row["to_node_id"]))

    for action in plan.edge_actions:
        if not isinstance(action, UpsertCanonicalEdge):
            continue
        from_node_id = _map_node_id(action.from_node_id, node_ids)
        to_node_id = _map_node_id(action.to_node_id, node_ids)
        _assert_active_node(
            conn,
            workspace_id=plan.workspace_id,
            node_id=from_node_id,
        )
        _assert_active_node(
            conn,
            workspace_id=plan.workspace_id,
            node_id=to_node_id,
        )
        relation = _get_relation_type(conn, action.relation_type)

        edge = conn.execute(
            """
            SELECT e.id, e.confidence, e.strength, e.details
            FROM memory_edge AS e
            WHERE e.workspace_id = %s
              AND e.from_node_id = %s
              AND e.to_node_id = %s
              AND e.relation_type_id = %s
              AND e.deleted_at IS NULL
            FOR UPDATE
            """,
            (plan.workspace_id, from_node_id, to_node_id, relation["id"]),
        ).fetchone()

        if edge is None and action.operation == "specialize":
            if action.previous_relation_type is None:
                raise ValueError("A specialize action needs previous_relation_type.")
            previous_relation = _get_relation_type(
                conn,
                action.previous_relation_type,
                allow_inactive=True,
            )
            # Rewrite the broader edge in place so its evidence, details, and
            # history carry over to the more specific relation.
            edge = conn.execute(
                """
                SELECT e.id, e.confidence, e.strength, e.details
                FROM memory_edge AS e
                WHERE e.workspace_id = %s
                  AND e.id = %s
                  AND e.relation_type_id = %s
                  AND e.deleted_at IS NULL
                FOR UPDATE
                """,
                (plan.workspace_id, action.edge_id, previous_relation["id"]),
            ).fetchone()
            if edge is None:
                raise StaleTransactionPlanError(
                    f"Edge {action.edge_id} selected for specialization "
                    "changed after planning."
                )
            conn.execute(
                """
                UPDATE memory_edge
                SET relation_type_id = %s,
                    from_node_id = %s,
                    to_node_id = %s,
                    updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (
                    relation["id"],
                    from_node_id,
                    to_node_id,
                    plan.workspace_id,
                    edge["id"],
                ),
            )

        incoming_confidence = combine_probabilities(
            [item.confidence for item in action.evidence]
        )
        if relation["is_exclusive"]:
            conflicts = conn.execute(
                """
                SELECT id, from_node_id, to_node_id
                FROM memory_edge
                WHERE workspace_id = %s
                  AND from_node_id = %s
                  AND relation_type_id = %s
                  AND to_node_id <> %s
                  AND deleted_at IS NULL
                FOR UPDATE
                """,
                (
                    plan.workspace_id,
                    from_node_id,
                    relation["id"],
                    to_node_id,
                ),
            ).fetchall()
            if conflicts and incoming_confidence < 0.90:
                raise StaleTransactionPlanError(
                    f"Exclusive relation {action.relation_type!r} changed after planning."
                )
            for conflict in conflicts:
                conn.execute(
                    """
                    UPDATE memory_edge
                    SET deleted_at = now(),
                        deleted_reason = 'superseded_non_temporal'
                    WHERE workspace_id = %s AND id = %s
                    """,
                    (plan.workspace_id, conflict["id"]),
                )
                affected_edge_ids.add(conflict["id"])
                changes.archived_edges.add(conflict["id"])
                affected_node_ids.update(
                    (conflict["from_node_id"], conflict["to_node_id"])
                )

        if edge is None:
            edge = conn.execute(
                """
                INSERT INTO memory_edge (
                    workspace_id,
                    id,
                    from_node_id,
                    to_node_id,
                    relation_type_id,
                    confidence,
                    strength
                )
                VALUES (%s, %s, %s, %s, %s, 0, 0)
                RETURNING id, confidence, strength, details
                """,
                (
                    plan.workspace_id,
                    action.edge_id,
                    from_node_id,
                    to_node_id,
                    relation["id"],
                ),
            ).fetchone()
            changes.created_edges.add(edge["id"])

        confidence_values, strength_values = _insert_evidence(
            conn,
            plan=plan,
            action=action,
            edge_id=edge["id"],
            node_ids=node_ids,
        )
        new_confidence = combine_probabilities(
            [edge["confidence"], *confidence_values]
        )
        new_strength = combine_probabilities([edge["strength"], *strength_values])
        new_details = combine_edge_details(edge["details"], action.details)
        conn.execute(
            """
            UPDATE memory_edge
            SET confidence = %s, strength = %s, details = %s, updated_at = now()
            WHERE workspace_id = %s AND id = %s
            """,
            (
                new_confidence,
                new_strength,
                new_details,
                plan.workspace_id,
                edge["id"],
            ),
        )
        affected_edge_ids.add(edge["id"])
        affected_node_ids.update((from_node_id, to_node_id))

    return affected_node_ids, affected_edge_ids


def _persist_review_items(
    conn: Any,
    *,
    run_id: UUID,
    plan: ResolutionTransactionPlan,
    result: dict[str, Any],
) -> None:
    extraction_items = result.get("repair_items", [])
    review_items: list[dict[str, Any]] = []
    resolved_relation_review_edge_ids = {
        item.proposed_edge_id
        for item in plan.repair_items
        if item.item_type
        in {
            "unsupported_relation_type",
            "invalid_relation_endpoints",
            "repaired_relation_endpoints",
        }
        and item.proposed_edge_id is not None
    }
    for index, item in enumerate(extraction_items):
        if not isinstance(item, dict):
            continue
        details = item.get("details")
        proposed_edge_id = (
            details.get("proposed_edge_id")
            if isinstance(details, dict)
            else None
        )
        if (
            item.get("item_type") == "unclear_relation"
            and proposed_edge_id in resolved_relation_review_edge_ids
        ):
            continue
        severity = (
            details.get("severity", "medium")
            if isinstance(details, dict)
            else "medium"
        )
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        review_items.append(
            {
                "review_key": f"{plan.source_id}:extraction:{index}",
                "phase": "extraction",
                "item_type": item.get("item_type") or "semantic_uncertainty",
                "severity": severity,
                "message": item.get("message") or "Extraction requires review.",
                "proposed_node_id": None,
                "proposed_edge_id": proposed_edge_id,
                "payload": item,
            }
        )

    for index, item in enumerate(plan.repair_items):
        review_items.append(
            {
                "review_key": f"{plan.source_id}:resolve:{index}",
                "phase": "resolve",
                "item_type": item.item_type,
                "severity": item.severity,
                "message": item.message,
                "proposed_node_id": item.proposed_node_id,
                "proposed_edge_id": item.proposed_edge_id,
                "payload": to_jsonable(item),
            }
        )

    for item in review_items:
        conn.execute(
            """
            INSERT INTO graph_review_item (
                workspace_id,
                ingestion_run_id,
                source_id,
                review_key,
                phase,
                item_type,
                severity,
                message,
                proposed_node_id,
                proposed_edge_id,
                payload_json
            )
            VALUES (
                %(workspace_id)s,
                %(run_id)s,
                %(source_id)s,
                %(review_key)s,
                %(phase)s,
                %(item_type)s,
                %(severity)s,
                %(message)s,
                %(proposed_node_id)s,
                %(proposed_edge_id)s,
                %(payload)s
            )
            ON CONFLICT (workspace_id, review_key) DO NOTHING
            """,
            {
                "workspace_id": plan.workspace_id,
                "run_id": run_id,
                "source_id": plan.source_id,
                **item,
                "payload": Jsonb(item["payload"]),
            },
        )


def _persist_ingestion_failure(
    *,
    run_id: UUID,
    workspace_id: UUID,
    source_id: UUID,
    phase: str,
    error: dict[str, Any],
    plan: ResolutionTransactionPlan | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Persist review context after the graph transaction has rolled back."""

    try:
        with get_connection() as conn:
            with conn.transaction():
                if plan is not None and result is not None:
                    _persist_review_items(
                        conn,
                        run_id=run_id,
                        plan=plan,
                        result=result,
                    )
                conn.execute(
                    """
                    INSERT INTO graph_review_item (
                        workspace_id,
                        ingestion_run_id,
                        source_id,
                        review_key,
                        phase,
                        item_type,
                        severity,
                        message,
                        payload_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, review_key) DO UPDATE SET
                        ingestion_run_id = EXCLUDED.ingestion_run_id,
                        phase = EXCLUDED.phase,
                        severity = EXCLUDED.severity,
                        status = 'pending',
                        message = EXCLUDED.message,
                        payload_json = EXCLUDED.payload_json,
                        resolved_at = NULL,
                        updated_at = now()
                    """,
                    (
                        workspace_id,
                        run_id,
                        source_id,
                        f"{source_id}:{phase}:ingestion-failure",
                        phase,
                        "ingestion_failure",
                        "high",
                        error.get("message") or "Ingestion failed and requires review.",
                        Jsonb(
                            {
                                "error": error,
                                "transaction_plan": (
                                    to_jsonable(plan) if plan is not None else None
                                ),
                            }
                        ),
                    ),
                )
    except Exception:
        # Preserve the original ingestion exception even if the review store is
        # unavailable; ingestion_run.error_json remains the fallback record.
        logger.exception(
            "Could not persist review item for failed ingestion run %s",
            run_id,
        )


def apply_transaction_plan(
    *,
    run_id: UUID,
    plan: ResolutionTransactionPlan,
    result: dict[str, Any],
) -> IngestionRunRecord:
    """Apply a resolution plan and complete its ingestion run in one commit."""

    with get_connection() as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{plan.workspace_id}:{plan.source_id}",),
            )
            run = conn.execute(
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
                FOR UPDATE
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise LookupError(f"Ingestion run {run_id} does not exist.")
            if run["workspace_id"] != plan.workspace_id or run["source_id"] != plan.source_id:
                raise ValueError("Transaction plan does not belong to the ingestion run.")
            if run["status"] == "completed":
                return IngestionRunRecord(**run)
            if run["status"] != "processing":
                raise StaleTransactionPlanError(
                    f"Ingestion run {run_id} is {run['status']!r}, not 'processing'."
                )

            changes = _RunGraphChanges()
            node_ids, affected_nodes = _apply_node_actions(
                conn, plan, changes
            )
            edge_nodes, affected_edges = _apply_edge_actions(
                conn, plan, node_ids, changes
            )
            affected_nodes.update(edge_nodes)
            _record_run_graph_changes(
                conn,
                run_id=run_id,
                workspace_id=plan.workspace_id,
                affected_nodes=affected_nodes,
                affected_edges=affected_edges,
                changes=changes,
            )
            _persist_review_items(
                conn,
                run_id=run_id,
                plan=plan,
                result=result,
            )

            for node_id in affected_nodes:
                _enqueue_projection_rebuild(
                    conn,
                    workspace_id=plan.workspace_id,
                    object_type="node",
                    object_id=node_id,
                )
            for edge_id in affected_edges:
                _enqueue_projection_rebuild(
                    conn,
                    workspace_id=plan.workspace_id,
                    object_type="edge",
                    object_id=edge_id,
                )

            conn.execute(
                """
                UPDATE memory_source
                SET preparation_status = 'ready', updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (plan.workspace_id, plan.source_id),
            )
            completed = conn.execute(
                """
                UPDATE ingestion_run
                SET
                    status = 'completed',
                    result_json = %s,
                    error_json = NULL,
                    updated_at = now()
                WHERE id = %s
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
                (Jsonb(result), run_id),
            ).fetchone()
    if completed is None:  # pragma: no cover - run is locked above.
        raise RuntimeError("PostgreSQL did not return the completed ingestion run.")
    return IngestionRunRecord(**completed)
