"""PostgreSQL persistence for workspaces and their memberships."""

from __future__ import annotations

from typing import Protocol, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.auth.models import User
from app.persistence.postgres.connection import get_async_connection
from app.workspaces.models import (
    CreatedWorkspace,
    MemberWorkspace,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)


class WorkspaceStore(Protocol):
    async def create_workspace(
        self,
        *,
        user: User,
        name: str,
        create_self_node: bool,
    ) -> CreatedWorkspace: ...

    async def list_member_workspaces(
        self,
        *,
        user_id: UUID,
    ) -> list[MemberWorkspace]: ...

    async def mark_last_used(self, *, workspace_id: UUID, user_id: UUID) -> bool: ...


class WorkspaceMembershipStore(Protocol):
    async def get_active_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceMembership | None: ...


async def insert_workspace(
    conn: AsyncConnection,
    *,
    user: User,
    name: str,
    create_self_node: bool,
    mark_last_used: bool = False,
) -> CreatedWorkspace:
    """Create a workspace owned by ``user`` inside the caller's transaction.

    Signup uses this to create a user's default workspace atomically with the
    account, marked as last used so the first login opens it; the workspace
    API uses it for additional workspaces.
    """

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

    member_row = await (
        await conn.execute(
            """
            INSERT INTO workspace_member (
                workspace_id, user_id, role, joined_at, last_used_at
            )
            VALUES (%s, %s, 'owner', now(), CASE WHEN %s THEN now() END)
            RETURNING role
            """,
            (workspace_id, user.id, mark_last_used),
        )
    ).fetchone()
    assert member_row is not None

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
    return CreatedWorkspace(
        workspace, cast(WorkspaceRole, member_row["role"]), self_node_id
    )


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
                return await insert_workspace(
                    conn,
                    user=user,
                    name=name,
                    create_self_node=create_self_node,
                )

    async def list_member_workspaces(
        self,
        *,
        user_id: UUID,
    ) -> list[MemberWorkspace]:
        async with get_async_connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT w.id, w.name, w.created_by_user_id, w.created_at,
                           wm.role, wm.last_used_at
                    FROM workspace_member AS wm
                    JOIN workspaces AS w
                      ON w.id = wm.workspace_id AND w.deleted_at IS NULL
                    WHERE wm.user_id = %s
                      AND wm.removed_at IS NULL
                    ORDER BY wm.joined_at NULLS LAST, w.created_at, w.id
                    """,
                    (user_id,),
                )
            ).fetchall()
        return [
            MemberWorkspace(
                workspace=Workspace(
                    id=row["id"],
                    name=row["name"],
                    created_by_user_id=row["created_by_user_id"],
                    created_at=row["created_at"],
                ),
                role=cast(WorkspaceRole, row["role"]),
                last_used_at=row["last_used_at"],
            )
            for row in rows
        ]

    async def mark_last_used(self, *, workspace_id: UUID, user_id: UUID) -> bool:
        """Record that the user opened this workspace; False without access."""

        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE workspace_member AS wm
                SET last_used_at = now()
                FROM workspaces AS w
                WHERE wm.workspace_id = %s
                  AND wm.user_id = %s
                  AND wm.removed_at IS NULL
                  AND w.id = wm.workspace_id
                  AND w.deleted_at IS NULL
                """,
                (workspace_id, user_id),
            )
        return cursor.rowcount > 0


class PostgresWorkspaceMembershipStore:
    async def get_active_membership(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
    ) -> WorkspaceMembership | None:
        async with get_async_connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT wm.workspace_id, wm.user_id, wm.role
                    FROM workspace_member AS wm
                    JOIN workspaces AS w
                      ON w.id = wm.workspace_id AND w.deleted_at IS NULL
                    WHERE wm.workspace_id = %s
                      AND wm.user_id = %s
                      AND wm.removed_at IS NULL
                    """,
                    (workspace_id, user_id),
                )
            ).fetchone()
        if row is None:
            return None
        return WorkspaceMembership(
            workspace_id=row["workspace_id"],
            user_id=row["user_id"],
            role=cast(WorkspaceRole, row["role"]),
        )
