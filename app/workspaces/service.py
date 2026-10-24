"""Workspace creation orchestration."""

from __future__ import annotations

from app.auth.models import AuthenticatedSession
from app.workspaces.models import CreatedWorkspace
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
