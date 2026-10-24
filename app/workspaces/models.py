"""Domain records for workspace creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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
