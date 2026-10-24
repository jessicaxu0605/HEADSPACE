"""Domain records for workspace creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


WorkspaceRole = Literal["owner", "editor", "viewer"]


@dataclass(frozen=True)
class Workspace:
    id: UUID
    name: str
    created_by_user_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class CreatedWorkspace:
    workspace: Workspace
    self_node_id: UUID | None


@dataclass(frozen=True)
class WorkspaceMembership:
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRole
