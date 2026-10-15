"""Claim and finish items in ``projection_rebuild_queue``."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.persistence.postgres.connection import get_async_connection


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
