"""FastAPI dependencies for workspace-scoped authorization."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status

from app.api.authentication import get_current_session
from app.auth.models import AuthenticatedSession
from app.workspaces.models import WorkspaceMembership
from app.workspaces.sql.postgres import (
    PostgresWorkspaceMembershipStore,
    WorkspaceMembershipStore,
)


@lru_cache(maxsize=1)
def get_workspace_membership_store() -> WorkspaceMembershipStore:
    return PostgresWorkspaceMembershipStore()


async def require_workspace_member(
    workspace_id: Annotated[
        UUID,
        Query(description="Workspace to authorize and scope this request to."),
    ],
    authenticated: AuthenticatedSession = Depends(get_current_session),
    store: WorkspaceMembershipStore = Depends(get_workspace_membership_store),
) -> WorkspaceMembership:
    """Require active membership in the workspace used by scoped endpoints."""

    membership = await store.get_active_membership(
        workspace_id=workspace_id,
        user_id=authenticated.user.id,
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active workspace membership required.",
        )
    return membership


async def require_workspace_writer(
    membership: WorkspaceMembership = Depends(require_workspace_member),
) -> WorkspaceMembership:
    """Require an active workspace membership that permits graph writes."""

    if membership.role not in {"owner", "editor"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace write access required.",
        )
    return membership
