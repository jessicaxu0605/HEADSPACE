from __future__ import annotations

from uuid import UUID, uuid4

from app.workspaces.models import WorkspaceMembership, WorkspaceRole


TEST_WORKSPACE_ID = UUID("11111111-1111-1111-1111-111111111111")


def workspace_membership(
    role: WorkspaceRole = "owner",
    *,
    user_id: UUID | None = None,
) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace_id=TEST_WORKSPACE_ID,
        user_id=user_id or uuid4(),
        role=role,
    )
