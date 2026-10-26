"""Domain records for workspaces and memberships."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


WorkspaceRole = Literal["owner", "editor", "viewer"]

# The owner's self node carries this alias so ingestion can resolve a
# context-less "I" to it with an exact identifier match.
SPACE_OWNER_ALIAS = "space owner"
SPACE_OWNER_SUMMARY = "The owner of this space."


@dataclass(frozen=True)
class Workspace:
    id: UUID
    name: str
    created_by_user_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class CreatedWorkspace:
    workspace: Workspace
    role: WorkspaceRole
    self_node_id: UUID | None


@dataclass(frozen=True)
class MemberWorkspace:
    """A workspace as seen by one of its active members."""

    workspace: Workspace
    role: WorkspaceRole
    last_used_at: datetime | None = None
    # When any of the workspace's memory nodes or edges last changed (its
    # creation time until then). Not the workspace row's own updated_at.
    last_edited_at: datetime | None = None


@dataclass(frozen=True)
class WorkspaceMembership:
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
