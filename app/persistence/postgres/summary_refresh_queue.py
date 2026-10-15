"""Claim, read, and finish items in ``node_summary_refresh_queue``."""

from __future__ import annotations

from uuid import UUID

from app.db import get_async_connection
from app.workflows.summaries.refresh_summaries import (
    NodeSummaryState,
    SummaryRefreshItem,
)


class PostgresSummaryRefreshStore:
    async def claim(self, *, limit: int) -> list[SummaryRefreshItem]:
        """Mark pending refreshes as processing, skipping nodes already in progress."""

        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE node_summary_refresh_queue AS q
                SET status = 'processing'
                FROM (
                    SELECT p.workspace_id, p.id
                    FROM node_summary_refresh_queue AS p
                    WHERE p.status = 'pending'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM node_summary_refresh_queue AS running
                          WHERE running.workspace_id = p.workspace_id
                            AND running.node_id = p.node_id
                            AND running.status = 'processing'
                      )
                    ORDER BY p.created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                ) AS claimed
                WHERE q.workspace_id = claimed.workspace_id AND q.id = claimed.id
                RETURNING q.id, q.workspace_id, q.node_id, q.incoming_summaries
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            SummaryRefreshItem(
                queue_id=row["id"],
                workspace_id=row["workspace_id"],
                node_id=row["node_id"],
                incoming_summaries=list(row["incoming_summaries"]),
            )
            for row in rows
        ]

    async def load_nodes(
        self, items: list[SummaryRefreshItem]
    ) -> dict[tuple[UUID, UUID], NodeSummaryState]:
        """Return the current state of each item's node that is still active."""

        if not items:
            return {}
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT n.workspace_id, n.id, n.node_type, n.name, n.summary
                FROM memory_node AS n
                JOIN unnest(%s::uuid[], %s::uuid[]) AS item(workspace_id, node_id)
                  ON n.workspace_id = item.workspace_id AND n.id = item.node_id
                WHERE n.deleted_at IS NULL
                  AND n.merged_into_node_id IS NULL
                """,
                (
                    [item.workspace_id for item in items],
                    [item.node_id for item in items],
                ),
            )
            rows = await cursor.fetchall()
        return {
            (row["workspace_id"], row["id"]): NodeSummaryState(
                node_type=row["node_type"],
                title=row["name"],
                summary=row["summary"],
            )
            for row in rows
        }

    async def save_summary(
        self, *, workspace_id: UUID, node_id: UUID, summary: str
    ) -> None:
        """Store the merged summary and rebuild the node's search projections."""

        async with get_async_connection() as conn:
            await conn.execute(
                """
                UPDATE memory_node
                SET summary = %s, updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (summary, workspace_id, node_id),
            )
            await conn.execute(
                """
                INSERT INTO projection_rebuild_queue (
                    workspace_id, object_type, object_id
                )
                VALUES (%s, 'node', %s)
                ON CONFLICT (workspace_id, object_type, object_id)
                  WHERE status IN ('pending', 'processing')
                DO NOTHING
                """,
                (workspace_id, node_id),
            )

    async def finish(
        self, *, workspace_id: UUID, queue_id: UUID, succeeded: bool
    ) -> None:
        async with get_async_connection() as conn:
            await conn.execute(
                """
                UPDATE node_summary_refresh_queue
                SET status = %s
                WHERE workspace_id = %s AND id = %s
                """,
                ("completed" if succeeded else "failed", workspace_id, queue_id),
            )
