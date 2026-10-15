"""PostgreSQL reads and writes for node search projections."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.db import get_async_connection
from app.search.node_projection import (
    NodeProjectionSource,
    NodeSearchProjection,
    ProjectionEdge,
    ProjectionExternalId,
    ProjectionIdentifier,
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


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"
