"""Canonical node search and graph-neighborhood routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.dependencies import get_neighborhood_store, get_node_search_store
from app.graph.queries.node_neighborhood import NeighborhoodNode, NodeNeighborhoodStore
from app.graph.queries.node_search import NodeSearchMatchKind, NodeSearchStore
from app.workflows.ingest.enrichment.fields import displayable_type_specific
from app.workflows.ingest.models import NodeType, ProposedExternalId
from app.api.workspace_access import require_workspace_member
from app.workspaces.models import WorkspaceMembership


router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


class NodeExternalIdRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=100)
    external_id: str = Field(min_length=1, max_length=500)

    @field_validator("provider", "external_id")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class NodeQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    node_type: NodeType | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)
    external_ids: list[NodeExternalIdRequest] = Field(
        default_factory=list,
        max_length=20,
    )
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        aliases: list[str] = []
        for value in values:
            stripped = value.strip()
            if not stripped:
                raise ValueError("aliases must not contain blank values")
            if len(stripped) > 500:
                raise ValueError("aliases must be at most 500 characters")
            if stripped not in aliases:
                aliases.append(stripped)
        return aliases


class NodeQueryMatchResponse(BaseModel):
    node_id: UUID
    node_type: str
    title: str
    identifiers: list[str]
    match_kind: NodeSearchMatchKind
    match_score: float
    reasons: list[str]


class NodeQueryResponse(BaseModel):
    query: str
    node_type: NodeType | None
    matches: list[NodeQueryMatchResponse]


class NodeDetailResponse(BaseModel):
    """A type-specific field worth showing on the node page."""

    name: str
    label: str
    kind: str
    value: Any


def node_details(
    node_type: str, type_specific: dict[str, Any] | None
) -> list[NodeDetailResponse]:
    return [
        NodeDetailResponse(name=spec.name, label=spec.label, kind=spec.kind, value=value)
        for spec, value in displayable_type_specific(node_type, type_specific)
    ]


class NodeExternalIdResponse(BaseModel):
    provider: str
    external_id: str


class NeighborhoodNodeResponse(BaseModel):
    node_id: UUID
    node_type: str
    title: str
    summary: str | None
    importance: float
    confidence: float
    distance: int
    # Identifiers other than the title and the external IDs below.
    aliases: list[str]
    external_ids: list[NodeExternalIdResponse]
    details: list[NodeDetailResponse]

    @classmethod
    def from_node(cls, node: NeighborhoodNode) -> NeighborhoodNodeResponse:
        fields = asdict(node)
        type_specific = fields.pop("type_specific")
        identifiers = fields.pop("identifiers")
        external_ids = fields.pop("external_ids")
        # Emails promoted out of titles are kept as both identifier and
        # external ID; show them once, as the external ID.
        hidden = {node.title.casefold()} | {
            value.casefold() for _, value in external_ids
        }
        aliases: list[str] = []
        for identifier in identifiers:
            if identifier.casefold() not in hidden:
                aliases.append(identifier)
                hidden.add(identifier.casefold())
        return cls(
            **fields,
            aliases=aliases,
            external_ids=[
                NodeExternalIdResponse(provider=provider, external_id=external_id)
                for provider, external_id in external_ids
            ],
            details=node_details(node.node_type, type_specific),
        )


class NeighborhoodEdgeResponse(BaseModel):
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    connector: str
    strength: float
    confidence: float
    details: str | None


class NodeNeighborhoodResponse(BaseModel):
    start_node_id: UUID
    depth: int
    nodes: list[NeighborhoodNodeResponse]
    edges: list[NeighborhoodEdgeResponse]


@router.post("/query", response_model=NodeQueryResponse)
async def query_node(
    request: NodeQueryRequest,
    _membership: WorkspaceMembership = Depends(require_workspace_member),
    search_store: NodeSearchStore = Depends(get_node_search_store),
) -> NodeQueryResponse:
    """Return canonical nodes related to the query, by keyword and meaning."""

    workspace_id = _membership.workspace_id
    results = await search_store.search_nodes(
        workspace_id=workspace_id,
        query=request.query,
        node_type=request.node_type,
        aliases=request.aliases,
        external_ids=[
            ProposedExternalId(item.provider, item.external_id)
            for item in request.external_ids
        ],
        limit=request.limit,
    )
    return NodeQueryResponse(
        query=request.query,
        node_type=request.node_type,
        matches=[
            NodeQueryMatchResponse(
                node_id=result.node_id,
                node_type=result.node_type,
                title=result.title,
                identifiers=result.identifiers,
                match_kind=result.match_kind,
                match_score=result.score,
                reasons=result.reasons,
            )
            for result in results
        ],
    )


@router.get("/{node_id}/graph", response_model=NodeNeighborhoodResponse)
async def read_node_neighborhood(
    node_id: UUID,
    depth: int = Query(default=1, ge=0, le=10),
    _membership: WorkspaceMembership = Depends(require_workspace_member),
    neighborhood_store: NodeNeighborhoodStore = Depends(get_neighborhood_store),
) -> NodeNeighborhoodResponse:
    """Return the active canonical graph within ``depth`` hops of a node."""

    workspace_id = _membership.workspace_id
    neighborhood = await neighborhood_store.get_node_neighborhood(
        workspace_id=workspace_id,
        start_node_id=node_id,
        depth=depth,
    )
    if neighborhood is None:
        raise HTTPException(status_code=404, detail="Node not found.")

    return NodeNeighborhoodResponse(
        start_node_id=neighborhood.start_node_id,
        depth=neighborhood.depth,
        nodes=[NeighborhoodNodeResponse.from_node(node) for node in neighborhood.nodes],
        edges=[NeighborhoodEdgeResponse(**asdict(edge)) for edge in neighborhood.edges],
    )
