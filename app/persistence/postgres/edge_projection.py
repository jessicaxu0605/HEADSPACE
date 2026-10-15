"""PostgreSQL reads and writes for edge search projections."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from app.db import get_async_connection
from app.persistence.postgres.node_projection import vector_literal
from app.search.edge_projection import (
    EdgeProjectionSource,
    EdgeSearchProjection,
    ProjectionAnnotation,
    ProjectionEvidence,
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
