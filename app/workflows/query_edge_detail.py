"""Read one canonical edge with its endpoints and supporting evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID


EdgeStatus = Literal["active", "scheduled", "expired", "deleted"]


@dataclass(frozen=True, slots=True)
class EdgeEndpointNode:
    node_id: UUID
    node_type: str
    title: str
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeEvidenceAnnotation:
    start: int
    length: int
    node_id: UUID


@dataclass(frozen=True, slots=True)
class EdgeEvidenceSource:
    source_id: UUID
    source_type: str
    source_metadata: dict[str, Any]
    # When the source was ingested.
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EdgeEvidence:
    evidence_id: UUID
    raw_content: str
    confidence: float
    strength_boost: float
    is_verbatim: bool
    created_at: datetime
    source: EdgeEvidenceSource
    annotations: list[EdgeEvidenceAnnotation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EdgeDetail:
    edge_id: UUID
    relation_type: str
    inverse_relation_type: str
    relation_description: str | None
    connector: str
    is_symmetric: bool
    details: str | None
    strength: float
    confidence: float
    status: EdgeStatus
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    deleted_reason: str | None
    from_node: EdgeEndpointNode
    to_node: EdgeEndpointNode
    evidence: list[EdgeEvidence] = field(default_factory=list)


class EdgeDetailStore(Protocol):
    async def get_edge_detail(
        self,
        *,
        workspace_id: UUID,
        edge_id: UUID,
    ) -> EdgeDetail | None: ...
