"""Choose memories to surface on the landing page and the subgraph shown for each.

Surfacing is split into swappable *sources* and one *ranking* step:

- a source answers one question ("which tasks are open?", "what happened on this
  day?") and returns anchors, each a node or an edge with a source-local score
- ``rank_anchors`` merges every source's anchors using the source's
  ``importance``: 1 pins the source's anchors to the top in score order; below
  1 is a weighted random draw, so more important sources usually show up higher
  and every load is slightly different
- the store expands each chosen anchor into a small subgraph: the anchor plus
  its strongest context edges, one evidence quote per edge, and optionally
  edges elsewhere in the graph that are semantically close (edge search
  projections)

Edges are first-class anchors because most remembered detail lives on edges
(details, validity, evidence), not on nodes.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from app.graph.queries.edge_detail import EdgeStatus


AnchorKind = Literal["node", "edge"]
EdgeRole = Literal["anchor", "context", "related"]

# Unpinned anchors with a zero score still get a small chance to be drawn.
_MIN_DRAW_WEIGHT = 0.02


@dataclass(frozen=True, slots=True)
class SurfacingContext:
    workspace_id: UUID
    now: datetime
    # IANA name; "today" and anniversaries are computed in this time zone.
    timezone: str = "UTC"
    # Nodes and edges the viewer saw recently. Random sources skip them;
    # pinned sources ignore them so open tasks never disappear.
    exclude_node_ids: frozenset[UUID] = frozenset()
    exclude_edge_ids: frozenset[UUID] = frozenset()
    # Needed for related-edge expansion; None skips it.
    embedding_model: str | None = None


@dataclass(frozen=True, slots=True)
class SurfaceAnchor:
    kind: AnchorKind
    id: UUID
    # Name of the source that produced the anchor ("open_tasks", "on_this_day").
    reason: str
    # Source-local relevance in [0, 1]; orders pinned anchors and weights draws.
    score: float
    # Short display hint for the card ("due tomorrow", "3 years ago today").
    note: str | None = None
    # The local date the anchor is about, such as a due date or event day.
    # Orders the upcoming list.
    when: date | None = None


class SurfacingSource(Protocol):
    """One candidate query. ``conn`` is the store's open database connection."""

    name: str

    async def fetch(
        self,
        conn: Any,
        ctx: SurfacingContext,
        limit: int,
    ) -> list[SurfaceAnchor]: ...


@dataclass(frozen=True, slots=True)
class SourceConfig:
    source: SurfacingSource
    # 1 pins this source's anchors above everything else; otherwise the
    # relative chance its anchors are drawn early.
    importance: float
    # Most cards this source may contribute.
    limit: int
    # Candidates fetched before the draw. A larger pool means more variety.
    pool_size: int | None = None

    @property
    def pinned(self) -> bool:
        return self.importance >= 1

    @property
    def fetch_limit(self) -> int:
        return self.pool_size or self.limit * 4


@dataclass(frozen=True, slots=True)
class ExpansionConfig:
    # Strongest edges added around the anchor besides the anchor edge.
    context_edges: int = 3
    # Semantically similar edges from elsewhere in the graph.
    related_edges: int = 1
    related_min_similarity: float = 0.6


@dataclass(frozen=True, slots=True)
class SurfacedNode:
    node_id: UUID
    node_type: str
    title: str
    summary: str | None = None
    importance: float = 0.5
    type_specific: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SurfacedEvidence:
    evidence_id: UUID
    raw_content: str
    is_verbatim: bool
    source_type: str
    source_created_at: datetime
    # (start, length, node_id) mentions resolved to nodes.
    annotations: list[tuple[int, int, UUID]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SurfacedEdge:
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    connector: str
    details: str | None
    strength: float
    confidence: float
    status: EdgeStatus
    valid_from: datetime | None
    valid_to: datetime | None
    role: EdgeRole
    evidence: SurfacedEvidence | None = None
    # Cosine similarity to the anchor, for related edges only.
    similarity: float | None = None


@dataclass(frozen=True, slots=True)
class SurfacedMemory:
    anchor: SurfaceAnchor
    pinned: bool
    nodes: list[SurfacedNode]
    edges: list[SurfacedEdge]

    def sentences(self) -> list[str]:
        """Plain template text for each edge, anchor first."""

        titles = {node.node_id: node.title for node in self.nodes}
        return [edge_sentence(edge, titles) for edge in self.edges]


class MemorySurfacingStore(Protocol):
    async def surface_upcoming(
        self,
        *,
        ctx: SurfacingContext,
        limit: int,
    ) -> list[SurfacedMemory]: ...

    async def surface_memories(
        self,
        *,
        ctx: SurfacingContext,
        sources: Sequence[SourceConfig],
        max_cards: int,
        expansion: ExpansionConfig = ExpansionConfig(),
        rng: random.Random | None = None,
    ) -> list[SurfacedMemory]: ...


@dataclass(frozen=True, slots=True)
class RankedAnchor:
    anchor: SurfaceAnchor
    pinned: bool
    # Index into the source list, for enforcing each source's limit.
    source_index: int


def rank_anchors(
    batches: Sequence[tuple[SourceConfig, Sequence[SurfaceAnchor]]],
    *,
    rng: random.Random | None = None,
) -> list[RankedAnchor]:
    """Order every anchor for display: pinned sources first, then a weighted draw.

    Pinned anchors keep source order, then score order, capped at the source's
    ``limit``. Unpinned anchors follow in a weighted random order with weight
    ``importance × score`` (Efraimidis–Spirakis keys). Unpinned anchors are not
    capped here: the caller enforces source limits and ``max_cards`` while it
    also skips cards that repeat an earlier card. An anchor already ranked from
    an earlier source is dropped.
    """

    rng = rng or random.Random()
    seen: set[tuple[AnchorKind, UUID]] = set()
    ranked: list[RankedAnchor] = []

    for index, (config, anchors) in enumerate(batches):
        if not config.pinned:
            continue
        taken = 0
        for anchor in sorted(anchors, key=lambda a: -a.score):
            if taken >= config.limit:
                break
            if (anchor.kind, anchor.id) in seen:
                continue
            seen.add((anchor.kind, anchor.id))
            ranked.append(RankedAnchor(anchor, pinned=True, source_index=index))
            taken += 1

    keyed: list[tuple[float, int, SurfaceAnchor]] = []
    for index, (config, anchors) in enumerate(batches):
        if config.pinned or config.importance <= 0:
            continue
        for anchor in anchors:
            weight = max(config.importance * anchor.score, _MIN_DRAW_WEIGHT)
            # u^(1/w) sorted descending is a weighted sample without replacement.
            keyed.append((rng.random() ** (1 / weight), index, anchor))
    keyed.sort(key=lambda item: -item[0])

    for _, index, anchor in keyed:
        if (anchor.kind, anchor.id) in seen:
            continue
        seen.add((anchor.kind, anchor.id))
        ranked.append(RankedAnchor(anchor, pinned=False, source_index=index))
    return ranked


def edge_sentence(edge: SurfacedEdge, titles: dict[UUID, str]) -> str:
    sentence = (
        f"{titles.get(edge.from_node_id, '?')} {edge.connector} "
        f"{titles.get(edge.to_node_id, '?')}"
    )
    if edge.details:
        sentence += f" ({edge.details})"
    return sentence


def recency_decay(age_hours: float, half_life_hours: float) -> float:
    return math.pow(0.5, max(age_hours, 0) / half_life_hours)


def relative_days(offset_days: int) -> str:
    if offset_days == 0:
        return "today"
    if offset_days == 1:
        return "tomorrow"
    if offset_days == -1:
        return "yesterday"
    if offset_days > 0:
        return f"in {offset_days} days"
    return f"{-offset_days} days ago"
