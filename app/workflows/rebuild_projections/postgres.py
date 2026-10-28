"""PostgreSQL persistence for the projection rebuild workflow."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from app.db.postgres import get_async_connection, vector_literal
from app.workflows.rebuild_projections.edge import (
    EdgeProjectionSource,
    EdgeSearchProjection,
    ProjectionAnnotation,
    ProjectionEvidence,
)
from app.workflows.rebuild_projections.node import (
    NodeProjectionSource,
    NodeSearchProjection,
    ProjectionEdge,
    ProjectionExternalId,
    ProjectionIdentifier,
)


QueueObjectType = Literal["node", "edge"]


class PostgresProjectionQueue:
    async def claim(
        self, *, object_type: QueueObjectType, limit: int
    ) -> list[tuple[UUID, UUID, UUID]]:
        """Mark pending rebuilds as processing; return (queue id, workspace, object)."""

        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE projection_rebuild_queue AS q
                SET status = 'processing', updated_at = now()
                FROM (
                    SELECT workspace_id, id
                    FROM projection_rebuild_queue
                    WHERE object_type = %s AND status = 'pending'
                    ORDER BY created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                ) AS claimed
                WHERE q.workspace_id = claimed.workspace_id AND q.id = claimed.id
                RETURNING q.id, q.workspace_id, q.object_id
                """,
                (object_type, limit),
            )
            rows = await cursor.fetchall()
        return [(row["id"], row["workspace_id"], row["object_id"]) for row in rows]

    async def finish(
        self, *, workspace_id: UUID, queue_id: UUID, succeeded: bool
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                UPDATE projection_rebuild_queue
                SET status = %s, updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                ("completed" if succeeded else "failed", workspace_id, queue_id),
            )


class PostgresNodeProjectionStore:
    async def load_node_projection_source(
        self, *, workspace_id: UUID, node_id: UUID
    ) -> NodeProjectionSource | None:
        params = {"workspace_id": workspace_id, "node_id": node_id}
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, node_type, name, summary, type_specific
                FROM memory_node
                WHERE workspace_id = %(workspace_id)s
                  AND id = %(node_id)s
                  AND deleted_at IS NULL
                  AND merged_into_node_id IS NULL
                """,
                params,
            )
            node = await cursor.fetchone()
            if node is None:
                return None

            cursor = await conn.execute(
                """
                SELECT identifier_text, scope, confidence
                FROM node_identifier
                WHERE workspace_id = %(workspace_id)s
                  AND node_id = %(node_id)s
                  AND deleted_at IS NULL
                """,
                params,
            )
            identifier_rows = await cursor.fetchall()

            cursor = await conn.execute(
                """
                SELECT provider, external_id, confidence
                FROM node_external_id
                WHERE workspace_id = %(workspace_id)s
                  AND node_id = %(node_id)s
                  AND deleted_at IS NULL
                """,
                params,
            )
            external_rows = await cursor.fetchall()

            # Expired and scheduled edges are kept with their tense connector;
            # they are still facts about the node.
            cursor = await conn.execute(
                """
                SELECT
                    e.id AS edge_id,
                    r.name AS relation_type,
                    CASE
                        WHEN e.valid_from IS NOT NULL AND e.valid_from > now()
                            THEN r.future_connector
                        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now()
                            THEN r.past_connector
                        ELSE r.connector
                    END AS connector,
                    e.from_node_id = %(node_id)s AS outgoing,
                    neighbor.name AS neighbor_name,
                    neighbor.node_type AS neighbor_type,
                    e.strength,
                    e.confidence,
                    e.details
                FROM memory_edge AS e
                JOIN relation_type AS r ON r.id = e.relation_type_id
                JOIN memory_node AS neighbor
                  ON neighbor.workspace_id = e.workspace_id
                 AND neighbor.id = CASE
                       WHEN e.from_node_id = %(node_id)s THEN e.to_node_id
                       ELSE e.from_node_id
                     END
                WHERE e.workspace_id = %(workspace_id)s
                  AND (e.from_node_id = %(node_id)s OR e.to_node_id = %(node_id)s)
                  AND e.from_node_id <> e.to_node_id
                  AND e.deleted_at IS NULL
                  AND neighbor.deleted_at IS NULL
                  AND neighbor.merged_into_node_id IS NULL
                """,
                params,
            )
            edge_rows = await cursor.fetchall()

        return NodeProjectionSource(
            node_id=node["id"],
            node_type=node["node_type"],
            name=node["name"],
            summary=node["summary"],
            type_specific=node["type_specific"],
            identifiers=[
                ProjectionIdentifier(
                    row["identifier_text"], row["scope"], row["confidence"]
                )
                for row in identifier_rows
            ],
            external_ids=[
                ProjectionExternalId(
                    row["provider"], row["external_id"], row["confidence"]
                )
                for row in external_rows
            ],
            edges=[
                ProjectionEdge(**{**row, "connector": row["connector"] or ""})
                for row in edge_rows
            ],
        )

    async def get_projection_hashes(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
        embedding_model: str,
        projection_version: int,
    ) -> dict[str, str]:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT projection_type, source_hash
                FROM node_search_projection
                WHERE workspace_id = %s
                  AND node_id = %s
                  AND embedding_model = %s
                  AND projection_version = %s
                """,
                (workspace_id, node_id, embedding_model, projection_version),
            )
            rows = await cursor.fetchall()
        return {row["projection_type"]: row["source_hash"] for row in rows}

    async def save_node_projections(
        self, *, workspace_id: UUID, projections: Sequence[NodeSearchProjection]
    ) -> None:
        async with get_async_connection() as conn:
            async with conn.transaction():
                for projection in projections:
                    await conn.execute(
                        """
                        INSERT INTO node_search_projection (
                            workspace_id, node_id, projection_type,
                            embedding_model, projection_version,
                            search_text, embedding_text, embedding, source_hash
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                        ON CONFLICT (
                            workspace_id, node_id, projection_type,
                            embedding_model, projection_version
                        )
                        DO UPDATE SET
                            search_text = EXCLUDED.search_text,
                            embedding_text = EXCLUDED.embedding_text,
                            embedding = EXCLUDED.embedding,
                            source_hash = EXCLUDED.source_hash,
                            indexed_at = now()
                        """,
                        (
                            workspace_id,
                            projection.node_id,
                            projection.projection_type,
                            projection.embedding_model,
                            projection.projection_version,
                            projection.search_text,
                            projection.embedding_text,
                            vector_literal(projection.embedding),
                            projection.source_hash,
                        ),
                    )
                    # Older text formats for the same model are superseded.
                    await conn.execute(
                        """
                        DELETE FROM node_search_projection
                        WHERE workspace_id = %s
                          AND node_id = %s
                          AND projection_type = %s
                          AND embedding_model = %s
                          AND projection_version < %s
                        """,
                        (
                            workspace_id,
                            projection.node_id,
                            projection.projection_type,
                            projection.embedding_model,
                            projection.projection_version,
                        ),
                    )

    async def delete_node_projections(
        self, *, workspace_id: UUID, node_id: UUID
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                DELETE FROM node_search_projection
                WHERE workspace_id = %s AND node_id = %s
                """,
                (workspace_id, node_id),
            )


class PostgresEdgeProjectionStore:
    async def load_edge_projection_source(
        self, *, workspace_id: UUID, edge_id: UUID
    ) -> EdgeProjectionSource | None:
        params = {"workspace_id": workspace_id, "edge_id": edge_id}
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    r.name AS relation_type,
                    CASE
                        WHEN e.valid_from IS NOT NULL AND e.valid_from > now()
                            THEN r.future_connector
                        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now()
                            THEN r.past_connector
                        ELSE r.connector
                    END AS connector,
                    r.description AS relation_description,
                    r.parent_name AS parent_relation,
                    e.details,
                    e.valid_from,
                    e.valid_to,
                    f.name AS from_name,
                    f.node_type AS from_type,
                    t.name AS to_name,
                    t.node_type AS to_type
                FROM memory_edge AS e
                JOIN relation_type AS r ON r.id = e.relation_type_id
                JOIN memory_node AS f
                  ON f.workspace_id = e.workspace_id AND f.id = e.from_node_id
                JOIN memory_node AS t
                  ON t.workspace_id = e.workspace_id AND t.id = e.to_node_id
                WHERE e.workspace_id = %(workspace_id)s
                  AND e.id = %(edge_id)s
                  AND e.deleted_at IS NULL
                  AND f.deleted_at IS NULL AND f.merged_into_node_id IS NULL
                  AND t.deleted_at IS NULL AND t.merged_into_node_id IS NULL
                """,
                params,
            )
            edge = await cursor.fetchone()
            if edge is None:
                return None

            cursor = await conn.execute(
                """
                SELECT id, raw_content, confidence, is_verbatim, created_at
                FROM memory_evidence
                WHERE workspace_id = %(workspace_id)s AND edge_id = %(edge_id)s
                """,
                params,
            )
            evidence_rows = await cursor.fetchall()

            cursor = await conn.execute(
                """
                SELECT a.evidence_id, a."start", a.length, n.name AS node_name
                FROM memory_evidence_annotation AS a
                JOIN memory_evidence AS ev
                  ON ev.workspace_id = a.workspace_id AND ev.id = a.evidence_id
                JOIN memory_node AS n
                  ON n.workspace_id = a.workspace_id AND n.id = a.node_id
                WHERE a.workspace_id = %(workspace_id)s
                  AND ev.edge_id = %(edge_id)s
                """,
                params,
            )
            annotation_rows = await cursor.fetchall()

        annotations: dict[UUID, list[ProjectionAnnotation]] = defaultdict(list)
        for row in annotation_rows:
            annotations[row["evidence_id"]].append(
                ProjectionAnnotation(row["start"], row["length"], row["node_name"])
            )
        return EdgeProjectionSource(
            edge_id=edge_id,
            relation_type=edge["relation_type"],
            connector=edge["connector"] or "",
            from_name=edge["from_name"],
            from_type=edge["from_type"],
            to_name=edge["to_name"],
            to_type=edge["to_type"],
            relation_description=edge["relation_description"],
            parent_relation=edge["parent_relation"],
            details=edge["details"],
            valid_from=edge["valid_from"],
            valid_to=edge["valid_to"],
            evidence=[
                ProjectionEvidence(
                    raw_content=row["raw_content"],
                    confidence=row["confidence"],
                    is_verbatim=row["is_verbatim"],
                    created_at=row["created_at"],
                    annotations=annotations.get(row["id"], []),
                )
                for row in evidence_rows
            ],
        )

    async def get_edge_projection_hash(
        self,
        *,
        workspace_id: UUID,
        edge_id: UUID,
        embedding_model: str,
        projection_version: int,
    ) -> str | None:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT source_hash
                FROM edge_search_projection
                WHERE workspace_id = %s
                  AND edge_id = %s
                  AND embedding_model = %s
                  AND projection_version = %s
                """,
                (workspace_id, edge_id, embedding_model, projection_version),
            )
            row = await cursor.fetchone()
        return row["source_hash"] if row else None

    async def save_edge_projection(
        self, *, workspace_id: UUID, projection: EdgeSearchProjection
    ) -> None:
        async with get_async_connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO edge_search_projection (
                        workspace_id, edge_id, embedding_model, projection_version,
                        search_text, embedding_text, embedding, source_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (
                        workspace_id, edge_id, embedding_model, projection_version
                    )
                    DO UPDATE SET
                        search_text = EXCLUDED.search_text,
                        embedding_text = EXCLUDED.embedding_text,
                        embedding = EXCLUDED.embedding,
                        source_hash = EXCLUDED.source_hash,
                        indexed_at = now()
                    """,
                    (
                        workspace_id,
                        projection.edge_id,
                        projection.embedding_model,
                        projection.projection_version,
                        projection.search_text,
                        projection.embedding_text,
                        vector_literal(projection.embedding),
                        projection.source_hash,
                    ),
                )
                # Older text formats for the same model are superseded.
                await conn.execute(
                    """
                    DELETE FROM edge_search_projection
                    WHERE workspace_id = %s
                      AND edge_id = %s
                      AND embedding_model = %s
                      AND projection_version < %s
                    """,
                    (
                        workspace_id,
                        projection.edge_id,
                        projection.embedding_model,
                        projection.projection_version,
                    ),
                )

    async def delete_edge_projections(
        self, *, workspace_id: UUID, edge_id: UUID
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                DELETE FROM edge_search_projection
                WHERE workspace_id = %s AND edge_id = %s
                """,
                (workspace_id, edge_id),
            )
