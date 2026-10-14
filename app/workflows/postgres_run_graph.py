"""PostgreSQL-backed reads for the graph changes recorded by an ingestion run."""

from __future__ import annotations

from uuid import UUID

from app.db import get_async_connection
from app.workflows.query_run_graph import RunGraph, RunGraphEdge, RunGraphNode


class PostgresRunGraphStore:
    async def get_run_graph(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> RunGraph | None:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, source_id, status
                FROM ingestion_run
                WHERE workspace_id = %(workspace_id)s AND id = %(run_id)s
                """,
                {"workspace_id": workspace_id, "run_id": run_id},
            )
            run = await cursor.fetchone()
            if run is None:
                return None

            cursor = await conn.execute(
                """
                SELECT
                    n.id AS node_id,
                    n.node_type,
                    n.name AS title,
                    n.summary,
                    n.importance,
                    n.confidence,
                    CASE
                        WHEN n.deleted_at IS NOT NULL THEN 'deleted'
                        WHEN n.merged_into_node_id IS NOT NULL THEN 'merged'
                        ELSE 'active'
                    END AS status,
                    n.merged_into_node_id,
                    c.change_kind
                FROM ingestion_run_graph_change AS c
                JOIN memory_node AS n
                  ON n.workspace_id = c.workspace_id AND n.id = c.object_id
                WHERE c.workspace_id = %(workspace_id)s
                  AND c.run_id = %(run_id)s
                  AND c.object_type = 'node'
                ORDER BY lower(n.name), n.id
                """,
                {"workspace_id": workspace_id, "run_id": run_id},
            )
            node_rows = await cursor.fetchall()

            cursor = await conn.execute(
                """
                SELECT
                    e.id AS edge_id,
                    e.from_node_id,
                    e.to_node_id,
                    r.name AS relation_type,
                    CASE
                        WHEN e.valid_from IS NOT NULL AND e.valid_from > now()
                            THEN r.future_connector
                        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now()
                            THEN r.past_connector
                        ELSE r.connector
                    END AS connector,
                    e.strength,
                    e.confidence,
                    e.details,
                    CASE
                        WHEN e.deleted_at IS NOT NULL THEN 'deleted'
                        WHEN e.valid_from IS NOT NULL AND e.valid_from > now() THEN 'scheduled'
                        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now() THEN 'expired'
                        ELSE 'active'
                    END AS status,
                    e.deleted_reason,
                    c.change_kind
                FROM ingestion_run_graph_change AS c
                JOIN memory_edge AS e
                  ON e.workspace_id = c.workspace_id AND e.id = c.object_id
                JOIN relation_type AS r ON r.id = e.relation_type_id
                WHERE c.workspace_id = %(workspace_id)s
                  AND c.run_id = %(run_id)s
                  AND c.object_type = 'edge'
                ORDER BY r.name, e.id
                """,
                {"workspace_id": workspace_id, "run_id": run_id},
            )
            edge_rows = await cursor.fetchall()

        return RunGraph(
            run_id=run["id"],
            source_id=run["source_id"],
            run_status=run["status"],
            nodes=[RunGraphNode(**row) for row in node_rows],
            edges=[
                RunGraphEdge(**{**row, "connector": row["connector"] or row["relation_type"]})
                for row in edge_rows
            ],
        )
