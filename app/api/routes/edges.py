"""Single-edge detail route."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_edge_detail_store
from app.graph.queries.edge_detail import EdgeDetailStore, EdgeStatus
from app.api.workspace_access import require_workspace_member
from app.workspaces.models import WorkspaceMembership


router = APIRouter(prefix="/api/v1/edges", tags=["edges"])


class EdgeEndpointNodeResponse(BaseModel):
    node_id: UUID
    node_type: str
    title: str
    summary: str | None


class EdgeEvidenceAnnotationResponse(BaseModel):
    start: int
    length: int
    node_id: UUID


class EdgeEvidenceSourceResponse(BaseModel):
    source_id: UUID
    source_type: str
    source_metadata: dict[str, Any]
    created_at: datetime


class EdgeEvidenceResponse(BaseModel):
    evidence_id: UUID
    raw_content: str
    confidence: float
    strength_boost: float
    is_verbatim: bool
    created_at: datetime
    source: EdgeEvidenceSourceResponse
    annotations: list[EdgeEvidenceAnnotationResponse]


class EdgeDetailResponse(BaseModel):
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
    from_node: EdgeEndpointNodeResponse
    to_node: EdgeEndpointNodeResponse
    evidence: list[EdgeEvidenceResponse]


@router.get("/{edge_id}", response_model=EdgeDetailResponse)
async def read_edge(
    edge_id: UUID,
    _membership: WorkspaceMembership = Depends(require_workspace_member),
    edge_store: EdgeDetailStore = Depends(get_edge_detail_store),
) -> EdgeDetailResponse:
    """Return an edge with both endpoint nodes and all supporting evidence.

    Archived edges are still returned, with their status, so links to them keep
    working.
    """

    edge = await edge_store.get_edge_detail(
        workspace_id=_membership.workspace_id,
        edge_id=edge_id,
    )
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found.")
    return EdgeDetailResponse.model_validate(asdict(edge))
