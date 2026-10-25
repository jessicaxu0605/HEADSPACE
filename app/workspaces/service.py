"""Workspace creation, renaming, membership listing, and last-used tracking."""

from __future__ import annotations

from uuid import UUID

from app.auth.models import AuthenticatedSession
from app.workspaces.models import CreatedWorkspace, MemberWorkspace
from app.workspaces.store import WorkspaceStore


class WorkspaceService:
    def __init__(self, *, store: WorkspaceStore) -> None:
        self._store = store

    async def create_workspace(
        self,
        *,
        authenticated: AuthenticatedSession,
        name: str,
        create_self_node: bool,
    ) -> CreatedWorkspace:
        return await self._store.create_workspace(
            user=authenticated.user,
            name=name,
            create_self_node=create_self_node,
        )

    async def list_workspaces(
        self,
        *,
        authenticated: AuthenticatedSession,
    ) -> list[MemberWorkspace]:
        return await self._store.list_member_workspaces(
            user_id=authenticated.user.id
        )

    async def mark_last_used(
        self,
        *,
        authenticated: AuthenticatedSession,
        workspace_id: UUID,
    ) -> bool:
        return await self._store.mark_last_used(
            workspace_id=workspace_id, user_id=authenticated.user.id
        )

    async def rename_workspace(
        self,
        *,
        authenticated: AuthenticatedSession,
        workspace_id: UUID,
        name: str,
    ) -> MemberWorkspace | None:
        return await self._store.rename_workspace(
            workspace_id=workspace_id, user_id=authenticated.user.id, name=name
        )
