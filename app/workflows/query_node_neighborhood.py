"""Read a bounded neighborhood from the canonical memory graph."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NeighborhoodNode:
    node_id: UUID
    node_type: str
    title: str
    summary: str | None = None
    importance: float = 0.5
    confidence: float = 0.5
    distance: int = 0


@dataclass(frozen=True, slots=True)
class NeighborhoodEdge:
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    strength: float
    confidence: float
    details: str | None = None
    connector: str = ""


@dataclass(frozen=True, slots=True)
class NodeNeighborhood:
    start_node_id: UUID
    depth: int
    nodes: list[NeighborhoodNode]
    edges: list[NeighborhoodEdge]


class NodeNeighborhoodStore(Protocol):
    async def get_node_neighborhood(
        self,
        *,
        workspace_id: UUID,
        start_node_id: UUID,
        depth: int,
    ) -> NodeNeighborhood | None: ...


class SimpleNodeNeighborhoodStore:
    """Bounded, cycle-safe traversal over a workspace-scoped graph snapshot."""

    def __init__(
        self,
        *,
        nodes: list[NeighborhoodNode] | None = None,
        edges: list[NeighborhoodEdge] | None = None,
    ) -> None:
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])

    async def get_node_neighborhood(
        self,
        *,
        workspace_id: UUID,
        start_node_id: UUID,
        depth: int,
    ) -> NodeNeighborhood | None:
        del workspace_id  # The supplied snapshot is already workspace-scoped.
        if depth < 0:
            raise ValueError("depth must not be negative")

        nodes_by_id = {node.node_id: node for node in self.nodes}
        if start_node_id not in nodes_by_id:
            return None

        adjacency: dict[UUID, set[UUID]] = {
            node_id: set() for node_id in nodes_by_id
        }
        edges_by_id: dict[UUID, NeighborhoodEdge] = {}
        for edge in self.edges:
            if (
                edge.from_node_id not in nodes_by_id
                or edge.to_node_id not in nodes_by_id
            ):
                continue
            edges_by_id.setdefault(edge.edge_id, edge)
            adjacency[edge.from_node_id].add(edge.to_node_id)
            adjacency[edge.to_node_id].add(edge.from_node_id)

        distances = {start_node_id: 0}
        pending = deque([start_node_id])
        while pending:
            node_id = pending.popleft()
            node_distance = distances[node_id]
            if node_distance >= depth:
                continue
            for neighbor_id in sorted(adjacency[node_id], key=str):
                if neighbor_id in distances:
                    continue
                distances[neighbor_id] = node_distance + 1
                pending.append(neighbor_id)

        included_ids = set(distances)
        nodes = [
            NeighborhoodNode(
                node_id=node.node_id,
                node_type=node.node_type,
                title=node.title,
                summary=node.summary,
                importance=node.importance,
                confidence=node.confidence,
                distance=distances[node.node_id],
            )
            for node in nodes_by_id.values()
            if node.node_id in included_ids
        ]
        nodes.sort(
            key=lambda node: (
                node.distance,
                node.title.casefold(),
                str(node.node_id),
            )
        )

        edges = [
            edge
            for edge in edges_by_id.values()
            if edge.from_node_id in included_ids and edge.to_node_id in included_ids
        ]
        edges.sort(key=lambda edge: str(edge.edge_id))
        return NodeNeighborhood(start_node_id, depth, nodes, edges)
