"""PostgreSQL-backed reads for bounded canonical graph neighborhoods."""

from __future__ import annotations

from uuid import UUID

from app.db import get_async_connection
from app.graph.queries.node_neighborhood import (
    NeighborhoodEdge,
    NeighborhoodNode,
    NodeNeighborhood,
)


class PostgresNodeNeighborhoodStore:
    async def get_node_neighborhood(
        self,
        *,
        workspace_id: UUID,
        start_node_id: UUID,
        depth: int,
    ) -> NodeNeighborhood | None:
        if depth < 0:
            raise ValueError("depth must not be negative")

        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                WITH RECURSIVE active_neighbors(node_id, neighbor_id) AS NOT MATERIALIZED (
                    SELECT e.from_node_id, e.to_node_id
                    FROM memory_edge AS e
                    WHERE e.workspace_id = %(workspace_id)s
                      AND e.deleted_at IS NULL
                      AND (e.valid_from IS NULL OR e.valid_from <= now())
                      AND (e.valid_to IS NULL OR e.valid_to > now())

                    UNION ALL

                    SELECT e.to_node_id, e.from_node_id
                    FROM memory_edge AS e
                    WHERE e.workspace_id = %(workspace_id)s
                      AND e.deleted_at IS NULL
                      AND (e.valid_from IS NULL OR e.valid_from <= now())
                      AND (e.valid_to IS NULL OR e.valid_to > now())
                ),
                walk(node_id, distance) AS (
                    SELECT n.id, 0
                    FROM memory_node AS n
                    WHERE n.workspace_id = %(workspace_id)s
                      AND n.id = %(start_node_id)s
                      AND n.deleted_at IS NULL
                      AND n.merged_into_node_id IS NULL

                    UNION

                    SELECT frontier.neighbor_id, walk.distance + 1
                    FROM walk
                    JOIN active_neighbors AS frontier
                      ON frontier.node_id = walk.node_id
                    JOIN memory_node AS neighbor
                      ON neighbor.workspace_id = %(workspace_id)s
                     AND neighbor.id = frontier.neighbor_id
                     AND neighbor.deleted_at IS NULL
                     AND neighbor.merged_into_node_id IS NULL
                    WHERE walk.distance < %(depth)s
                ),
                distances AS (
                    SELECT node_id, min(distance)::integer AS distance
                    FROM walk
                    GROUP BY node_id
                ),
                neighborhood_nodes AS (
                    SELECT
                        n.id AS node_id,
                        n.node_type,
                        n.name AS title,
                        n.summary,
                        n.importance,
                        n.confidence,
                        d.distance
                    FROM distances AS d
                    JOIN memory_node AS n
                      ON n.workspace_id = %(workspace_id)s
                     AND n.id = d.node_id
                ),
                neighborhood_edges AS (
                    SELECT
                        e.id AS edge_id,
                        e.from_node_id,
                        e.to_node_id,
                        r.name AS relation_type,
                        r.connector,
                        e.strength,
                        e.confidence,
                        e.details
                    FROM memory_edge AS e
                    JOIN distances AS from_distance
                      ON from_distance.node_id = e.from_node_id
                    JOIN distances AS to_distance
                      ON to_distance.node_id = e.to_node_id
                    JOIN relation_type AS r ON r.id = e.relation_type_id
                    WHERE e.workspace_id = %(workspace_id)s
                      AND e.deleted_at IS NULL
                      AND (e.valid_from IS NULL OR e.valid_from <= now())
                      AND (e.valid_to IS NULL OR e.valid_to > now())
                )
                SELECT
                    'node'::text AS record_kind,
                    0 AS record_order,
                    n.node_id,
                    n.node_type,
                    n.title,
                    n.summary,
                    n.importance,
                    n.confidence,
                    n.distance,
                    NULL::uuid AS edge_id,
                    NULL::uuid AS from_node_id,
                    NULL::uuid AS to_node_id,
                    NULL::text AS relation_type,
                    NULL::text AS connector,
                    NULL::double precision AS strength,
                    NULL::text AS details
                FROM neighborhood_nodes AS n

                UNION ALL

                SELECT
                    'edge'::text,
                    1,
                    NULL::uuid,
                    NULL::text,
                    NULL::text,
                    NULL::text,
                    NULL::double precision,
                    e.confidence,
                    NULL::integer,
                    e.edge_id,
                    e.from_node_id,
                    e.to_node_id,
                    e.relation_type,
                    e.connector,
                    e.strength,
                    e.details
                FROM neighborhood_edges AS e
                ORDER BY
                    record_order,
                    distance NULLS LAST,
                    title NULLS LAST,
                    node_id NULLS LAST,
                    edge_id NULLS LAST
                """,
                {
                    "workspace_id": workspace_id,
                    "start_node_id": start_node_id,
                    "depth": depth,
                },
            )
            rows = await cursor.fetchall()

        if not rows:
            return None

        nodes = [
            NeighborhoodNode(
                node_id=row["node_id"],
                node_type=row["node_type"],
                title=row["title"],
                summary=row["summary"],
                importance=row["importance"],
                confidence=row["confidence"],
                distance=row["distance"],
            )
            for row in rows
            if row["record_kind"] == "node"
        ]
        edges = [
            NeighborhoodEdge(
                edge_id=row["edge_id"],
                from_node_id=row["from_node_id"],
                to_node_id=row["to_node_id"],
                relation_type=row["relation_type"],
                connector=row["connector"],
                strength=row["strength"],
                confidence=row["confidence"],
                details=row["details"],
            )
            for row in rows
            if row["record_kind"] == "edge"
        ]
        return NodeNeighborhood(start_node_id, depth, nodes, edges)
