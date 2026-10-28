"""Landing-page memory surfacing route."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.dependencies import get_embedding_model, get_memory_surfacing_store
from app.api.routes.edges import EdgeEvidenceAnnotationResponse
from app.api.routes.nodes import NodeDetailResponse, node_details
from app.graph.queries.edge_detail import EdgeStatus
from app.graph.queries.memory_surfacing import (
    AnchorKind,
    EdgeRole,
    MemorySurfacingStore,
    SurfacedMemory,
    SurfacingContext,
    edge_sentence,
)
from app.api.workspace_access import require_workspace_member
from app.workspaces.models import WorkspaceMembership


router = APIRouter(prefix="/api/v1/landing", tags=["landing"])


class LandingAnchorResponse(BaseModel):
    kind: AnchorKind
    id: UUID
    # Source that chose the anchor, such as "open_tasks" or "on_this_day".
    reason: str
    score: float
    # Short display hint, such as "due tomorrow" or "3 years ago today".
    note: str | None
    # Local date the anchor is about, such as a due date or event day.
    when: date | None


class LandingNodeResponse(BaseModel):
    node_id: UUID
    node_type: str
    title: str
    summary: str | None
    importance: float
    details: list[NodeDetailResponse]


class LandingEvidenceResponse(BaseModel):
    evidence_id: UUID
    raw_content: str
    is_verbatim: bool
    source_type: str
    source_created_at: datetime
    annotations: list[EdgeEvidenceAnnotationResponse]


class LandingEdgeResponse(BaseModel):
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
    # anchor: the surfaced edge; context: around the anchor; related:
    # semantically close edge from elsewhere in the graph.
    role: EdgeRole
    similarity: float | None
    # "{from} {connector} {to} (details)", for display without an LLM.
    sentence: str
    evidence: LandingEvidenceResponse | None


class LandingMemoryResponse(BaseModel):
    anchor: LandingAnchorResponse
    pinned: bool
    # Anchor node (or the anchor edge's endpoints) first.
    nodes: list[LandingNodeResponse]
    # Anchor edge first, then context, then related edges.
    edges: list[LandingEdgeResponse]

    @classmethod
    def from_memory(cls, memory: SurfacedMemory) -> LandingMemoryResponse:
        titles = {node.node_id: node.title for node in memory.nodes}
        return cls(
            anchor=LandingAnchorResponse(**asdict(memory.anchor)),
            pinned=memory.pinned,
            nodes=[
                LandingNodeResponse(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    title=node.title,
                    summary=node.summary,
                    importance=node.importance,
                    details=node_details(node.node_type, node.type_specific),
                )
                for node in memory.nodes
            ],
            edges=[
                LandingEdgeResponse(
                    edge_id=edge.edge_id,
                    from_node_id=edge.from_node_id,
                    to_node_id=edge.to_node_id,
                    relation_type=edge.relation_type,
                    connector=edge.connector,
                    details=edge.details,
                    strength=edge.strength,
                    confidence=edge.confidence,
                    status=edge.status,
                    valid_from=edge.valid_from,
                    valid_to=edge.valid_to,
                    role=edge.role,
                    similarity=edge.similarity,
                    sentence=edge_sentence(edge, titles),
                    evidence=(
                        None
                        if edge.evidence is None
                        else LandingEvidenceResponse(
                            evidence_id=edge.evidence.evidence_id,
                            raw_content=edge.evidence.raw_content,
                            is_verbatim=edge.evidence.is_verbatim,
                            source_type=edge.evidence.source_type,
                            source_created_at=edge.evidence.source_created_at,
                            annotations=[
                                EdgeEvidenceAnnotationResponse(
                                    start=start, length=length, node_id=node_id
                                )
                                for start, length, node_id in edge.evidence.annotations
                            ],
                        )
                    ),
                )
                for edge in memory.edges
            ],
        )


class LandingResponse(BaseModel):
    generated_at: datetime
    timezone: str
    # Unfinished tasks and upcoming events in date order, each with its
    # adjacent edges and nodes.
    upcoming: list[LandingMemoryResponse]
    # A varied draw of memories.
    memories: list[LandingMemoryResponse]


@router.get("", response_model=LandingResponse)
async def read_landing(
    max_cards: int = Query(default=6, ge=0, le=20),
    max_upcoming: int = Query(default=8, ge=0, le=50),
    timezone: str = Query(
        default="UTC",
        description="IANA time zone used for today, anniversaries, and due dates.",
    ),
    exclude_node_ids: list[UUID] = Query(default=[]),
    exclude_edge_ids: list[UUID] = Query(default=[]),
    _membership: WorkspaceMembership = Depends(require_workspace_member),
    surfacing_store: MemorySurfacingStore = Depends(get_memory_surfacing_store),
    embedding_model: str = Depends(get_embedding_model),
) -> LandingResponse:
    """Return open tasks and a varied set of memories for the landing page.

    Upcoming items are unfinished tasks and events in the next two weeks, in
    date order, each with only its adjacent edges and nodes. Memories are a
    fresh weighted draw on each call; pass recently shown node and edge IDs to
    keep them out of it. Upcoming items are never excluded.
    """

    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Unknown time zone.") from exc

    ctx = SurfacingContext(
        workspace_id=_membership.workspace_id,
        now=datetime.now(UTC),
        timezone=timezone,
        exclude_node_ids=frozenset(exclude_node_ids),
        exclude_edge_ids=frozenset(exclude_edge_ids),
        embedding_model=embedding_model,
    )
    upcoming, memories = await surfacing_store.surface_landing(
        ctx=ctx,
        max_upcoming=max_upcoming,
        max_cards=max_cards,
    )
    return LandingResponse(
        generated_at=ctx.now,
        timezone=timezone,
        upcoming=[LandingMemoryResponse.from_memory(item) for item in upcoming],
        memories=[LandingMemoryResponse.from_memory(memory) for memory in memories],
    )
