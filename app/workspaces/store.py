"""PostgreSQL persistence for atomic workspace creation."""

from __future__ import annotations

from typing import Protocol

from psycopg.types.json import Jsonb

from app.auth.models import User
from app.persistence.postgres.connection import get_async_connection
from app.workspaces.models import CreatedWorkspace, Workspace


class WorkspaceStore(Protocol):
    async def create_workspace(
        self,
        *,
        user: User,
        name: str,
        create_self_node: bool,
    ) -> CreatedWorkspace: ...

class PostgresWorkspaceStore:
    async def create_workspace(
        self,
        *,
        user: User,
        name: str,
        create_self_node: bool,
    ) -> CreatedWorkspace:
        async with get_async_connection() as conn:
            async with conn.transaction():
                workspace_row = await (
                    await conn.execute(
                        """
                        INSERT INTO workspaces (name, created_by_user_id)
                        VALUES (%s, %s)
                        RETURNING id, name, created_by_user_id, created_at
                        """,
                        (name, user.id),
                    )
                ).fetchone()
                assert workspace_row is not None
                workspace_id = workspace_row["id"]

                await conn.execute(
                    """
                    INSERT INTO workspace_member (
                        workspace_id, user_id, role, joined_at
                    )
                    VALUES (%s, %s, 'owner', now())
                    """,
                    (workspace_id, user.id),
                )

                self_node_id = None
                if create_self_node:
                    profile = {}
                    if user.birthday is not None:
                        profile["birthday"] = user.birthday.isoformat()
                    node_row = await (
                        await conn.execute(
                            """
                            INSERT INTO memory_node (
                                workspace_id, node_type, name, summary,
                                type_specific, importance, confidence,
                                creation_idempotency_key
                            )
                            VALUES (%s, 'person', %s, %s, %s, 1.0, 1.0, %s)
                            RETURNING id
                            """,
                            (
                                workspace_id,
                                user.name,
                                user.about,
                                Jsonb(profile) if profile else None,
                                f"workspace-self:{user.id}",
                            ),
                        )
                    ).fetchone()
                    assert node_row is not None
                    self_node_id = node_row["id"]
                    await conn.execute(
                        """
                        INSERT INTO node_external_id (
                            workspace_id, node_id, provider, external_id, confidence
                        )
                        VALUES (%s, %s, 'headspace_user', %s, 1.0)
                        """,
                        (workspace_id, self_node_id, str(user.id)),
                    )
                    await conn.execute(
                        """
                        INSERT INTO projection_rebuild_queue (
                            workspace_id, object_type, object_id
                        )
                        VALUES (%s, 'node', %s)
                        """,
                        (workspace_id, self_node_id),
                    )

        workspace = Workspace(
            id=workspace_row["id"],
            name=workspace_row["name"],
            created_by_user_id=workspace_row["created_by_user_id"],
            created_at=workspace_row["created_at"],
        )
        return CreatedWorkspace(workspace, self_node_id)
