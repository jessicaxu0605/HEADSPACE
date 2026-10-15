"""PostgreSQL-backed reads for a single canonical edge."""

from __future__ import annotations

from uuid import UUID

from app.persistence.postgres.connection import get_async_connection
from app.graph.queries.edge_detail import (
    EdgeDetail,
    EdgeEndpointNode,
    EdgeEvidence,
    EdgeEvidenceAnnotation,
    EdgeEvidenceSource,
)


class PostgresEdgeDetailStore:
    async def get_edge_detail(
        self,
        *,
        workspace_id: UUID,
        edge_id: UUID,
    ) -> EdgeDetail | None:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    e.id AS edge_id,
                    r.name AS relation_type,
                    r.inverse_name AS inverse_relation_type,
                    r.description AS relation_description,
                    CASE
                        WHEN e.valid_from IS NOT NULL AND e.valid_from > now()
                            THEN r.future_connector
                        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now()
                            THEN r.past_connector
                        ELSE r.connector
                    END AS connector,
                    r.is_symmetric,
                    e.details,
                    e.strength,
                    e.confidence,
                    CASE
                        WHEN e.deleted_at IS NOT NULL THEN 'deleted'
                        WHEN e.valid_from IS NOT NULL AND e.valid_from > now() THEN 'scheduled'
                        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now() THEN 'expired'
                        ELSE 'active'
                    END AS status,
                    e.valid_from,
                    e.valid_to,
                    e.created_at,
                    e.updated_at,
                    e.deleted_at,
                    e.deleted_reason,
                    f.id AS from_node_id,
                    f.node_type AS from_node_type,
                    f.name AS from_title,
                    f.summary AS from_summary,
                    t.id AS to_node_id,
                    t.node_type AS to_node_type,
                    t.name AS to_title,
                    t.summary AS to_summary
                FROM memory_edge AS e
                JOIN relation_type AS r ON r.id = e.relation_type_id
                JOIN memory_node AS f
                  ON f.workspace_id = e.workspace_id AND f.id = e.from_node_id
                JOIN memory_node AS t
                  ON t.workspace_id = e.workspace_id AND t.id = e.to_node_id
                WHERE e.workspace_id = %(workspace_id)s
                  AND e.id = %(edge_id)s
                """,
                {"workspace_id": workspace_id, "edge_id": edge_id},
            )
            edge = await cursor.fetchone()
            if edge is None:
                return None

            cursor = await conn.execute(
                """
                SELECT
                    ev.id AS evidence_id,
                    ev.raw_content,
                    ev.confidence,
                    ev.strength_boost,
                    ev.is_verbatim,
                    ev.created_at,
                    s.id AS source_id,
                    s.source_type,
                    s.source_metadata,
                    s.created_at AS source_created_at,
                    COALESCE(
                        (
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'start', a."start",
                                    'length', a.length,
                                    'node_id', a.node_id
                                )
                                ORDER BY a."start", a.length
                            )
                            FROM memory_evidence_annotation AS a
                            WHERE a.workspace_id = ev.workspace_id
                              AND a.evidence_id = ev.id
                        ),
                        '[]'::jsonb
                    ) AS annotations
                FROM memory_evidence AS ev
                JOIN memory_source AS s
                  ON s.workspace_id = ev.workspace_id AND s.id = ev.source_id
                WHERE ev.workspace_id = %(workspace_id)s
                  AND ev.edge_id = %(edge_id)s
                ORDER BY s.created_at DESC, ev.created_at DESC, ev.id
                """,
                {"workspace_id": workspace_id, "edge_id": edge_id},
            )
            evidence_rows = await cursor.fetchall()

        return EdgeDetail(
            edge_id=edge["edge_id"],
            relation_type=edge["relation_type"],
            inverse_relation_type=edge["inverse_relation_type"],
            relation_description=edge["relation_description"],
            connector=edge["connector"],
            is_symmetric=edge["is_symmetric"],
            details=edge["details"],
            strength=edge["strength"],
            confidence=edge["confidence"],
            status=edge["status"],
            valid_from=edge["valid_from"],
            valid_to=edge["valid_to"],
            created_at=edge["created_at"],
            updated_at=edge["updated_at"],
            deleted_at=edge["deleted_at"],
            deleted_reason=edge["deleted_reason"],
            from_node=EdgeEndpointNode(
                node_id=edge["from_node_id"],
                node_type=edge["from_node_type"],
                title=edge["from_title"],
                summary=edge["from_summary"],
            ),
            to_node=EdgeEndpointNode(
                node_id=edge["to_node_id"],
                node_type=edge["to_node_type"],
                title=edge["to_title"],
                summary=edge["to_summary"],
            ),
            evidence=[
                EdgeEvidence(
                    evidence_id=row["evidence_id"],
                    raw_content=row["raw_content"],
                    confidence=row["confidence"],
                    strength_boost=row["strength_boost"],
                    is_verbatim=row["is_verbatim"],
                    created_at=row["created_at"],
                    source=EdgeEvidenceSource(
                        source_id=row["source_id"],
                        source_type=row["source_type"],
                        source_metadata=row["source_metadata"] or {},
                        created_at=row["source_created_at"],
                    ),
                    annotations=[
                        EdgeEvidenceAnnotation(
                            start=item["start"],
                            length=item["length"],
                            node_id=UUID(item["node_id"]),
                        )
                        for item in row["annotations"]
                    ],
                )
                for row in evidence_rows
            ],
        )
