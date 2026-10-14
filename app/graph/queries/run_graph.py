"""Read the canonical nodes and edges an ingestion run created or changed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from app.graph.queries.edge_detail import EdgeStatus


RunGraphChangeKind = Literal["created", "updated", "archived", "merged"]
RunNodeStatus = Literal["active", "merged", "deleted"]


@dataclass(frozen=True, slots=True)
class RunGraphNode:
    node_id: UUID
    node_type: str
    title: str
    summary: str | None
    importance: float
    confidence: float
    status: RunNodeStatus
    merged_into_node_id: UUID | None
    change_kind: RunGraphChangeKind


@dataclass(frozen=True, slots=True)
class RunGraphEdge:
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    connector: str
    strength: float
    confidence: float
    details: str | None
    status: EdgeStatus
    deleted_reason: str | None
    change_kind: RunGraphChangeKind


@dataclass(frozen=True, slots=True)
class RunGraph:
    run_id: UUID
    source_id: UUID
    run_status: str
    nodes: list[RunGraphNode] = field(default_factory=list)
    edges: list[RunGraphEdge] = field(default_factory=list)


class RunGraphStore(Protocol):
    async def get_run_graph(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> RunGraph | None: ...
