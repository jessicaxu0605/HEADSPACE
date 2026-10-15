"""Nearest-neighbor lookups over ``node_search_projection``.

Shared retrieval primitive: ingestion resolution reads identity projections to
find possible duplicates, and client search reads identity and profile
projections to find related nodes. Each caller applies its own thresholds and
ranking.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.db import get_async_connection
from app.persistence.postgres.node_projection import vector_literal
from app.search.node_projection import NODE_PROJECTION_VERSION, ProjectionType


# Candidates fetched from the HNSW index before workspace and type filters;
# pgvector filters after the index scan, so ask for more than the caller needs.
_OVERFETCH = 4


@dataclass(frozen=True, slots=True)
class ProjectionMatch:
    node_id: UUID
    projection_type: ProjectionType
    similarity: float


async def nearest_node_projections(
    *,
    workspace_id: UUID,
    vector: Sequence[float],
    embedding_model: str,
    projection_types: Sequence[ProjectionType],
    node_type: str | None,
    limit: int,
    min_similarity: float,
) -> list[ProjectionMatch]:
    """Return active nodes' projections most similar to ``vector``, best first."""

    node_type_filter = "AND n.node_type = %(node_type)s" if node_type else ""
    async with get_async_connection() as conn:
        cursor = await conn.execute(
            f"""
            WITH nearest AS (
                SELECT
                    p.workspace_id,
                    p.node_id,
                    p.projection_type,
                    1 - (p.embedding <=> %(vector)s::vector) AS similarity
                FROM node_search_projection AS p
                WHERE p.workspace_id = %(workspace_id)s
                  AND p.embedding_model = %(embedding_model)s
                  AND p.projection_version = %(projection_version)s
                  AND p.projection_type = ANY(%(projection_types)s)
                ORDER BY p.embedding <=> %(vector)s::vector
                LIMIT %(fetch)s
            )
            SELECT nearest.node_id, nearest.projection_type, nearest.similarity
            FROM nearest
            JOIN memory_node AS n
              ON n.workspace_id = nearest.workspace_id
             AND n.id = nearest.node_id
             AND n.deleted_at IS NULL
             AND n.merged_into_node_id IS NULL
             {node_type_filter}
            WHERE nearest.similarity >= %(min_similarity)s
            ORDER BY nearest.similarity DESC, nearest.node_id
            """,
            {
                "workspace_id": workspace_id,
                "vector": vector_literal(vector),
                "embedding_model": embedding_model,
                "projection_version": NODE_PROJECTION_VERSION,
                "projection_types": list(projection_types),
                "node_type": node_type,
                "fetch": limit * _OVERFETCH,
                "min_similarity": min_similarity,
            },
        )
        rows = await cursor.fetchall()
    return [
        ProjectionMatch(row["node_id"], row["projection_type"], row["similarity"])
        for row in rows
    ][:limit]
