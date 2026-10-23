"""Workspace creation orchestration."""

from __future__ import annotations

from app.auth.models import AuthenticatedSession
from app.auth.tokens import JwtTokenCodec
from app.workspaces.models import IssuedWorkspace
from app.workspaces.store import WorkspaceStore


class WorkspaceService:
    def __init__(self, *, store: WorkspaceStore, tokens: JwtTokenCodec) -> None:
        self._store = store
        self._tokens = tokens

    async def create_workspace(
        self,
        *,
        authenticated: AuthenticatedSession,
        name: str,
        create_self_node: bool,
    ) -> IssuedWorkspace:
        created = await self._store.create_workspace(
            user=authenticated.user,
            session_id=authenticated.session.id,
            name=name,
            create_self_node=create_self_node,
        )
        return IssuedWorkspace(created, self._tokens.encode(created.session))
