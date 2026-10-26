"""Source ingestion routes.

Ingestion runs inline in the request: extraction, enrichment, resolution, and
the canonical graph transaction all complete before the response is sent.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.dependencies import (
    get_enrichment_llm,
    get_extraction_llm,
    get_resolver_store,
    get_run_graph_store,
)
from app.graph.queries.edge_detail import EdgeStatus
from app.graph.queries.run_graph import (
    RunGraphChangeKind,
    RunGraphStore,
    RunNodeStatus,
)
from app.workflows.ingest.enrich_type_specific import TypeSpecificEnrichmentLLM
from app.workflows.ingest.extract_proposed_subgraph import ChunkExtractionLLM
from app.workflows.ingest.resolve_proposed_subgraph import CanonicalGraphResolverStore
from app.workflows.ingest.types import IngestionRunRecord, MAX_LOAD_ALL_BYTES
from app.workflows.ingest.workflow import (
    IngestionExecutionError,
    get_ingestion_run,
    ingest_content,
)
from app.workspaces.dependencies import (
    require_workspace_member,
    require_workspace_writer,
)
from app.workspaces.models import WorkspaceMembership


router = APIRouter(prefix="/api/v1/ingestions", tags=["ingestion"])


SupportedApiSourceType = Literal["manual_entry", "email", "calendar_event"]
class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: SupportedApiSourceType = "manual_entry"
    content: str = Field(min_length=1)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str = Field(min_length=1, max_length=200)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must contain non-whitespace text")
        if len(value.encode("utf-8")) > MAX_LOAD_ALL_BYTES:
            raise ValueError(
                f"content must be at most {MAX_LOAD_ALL_BYTES} UTF-8 bytes"
            )
        return value

    @field_validator("client_request_id")
    @classmethod
    def validate_client_request_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("client_request_id must not be blank")
        return stripped


class IngestionRunResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    source_id: UUID
    status: Literal["pending", "processing", "completed", "failed"]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: str
    updated_at: str

    @classmethod
    def from_record(cls, run: IngestionRunRecord) -> "IngestionRunResponse":
        return cls(
            id=run.id,
            workspace_id=run.workspace_id,
            source_id=run.source_id,
            status=cast(Any, run.status),
            result=run.result,
            error=run.error,
            created_at=run.created_at.isoformat(),
            updated_at=run.updated_at.isoformat(),
        )


class RunGraphNodeResponse(BaseModel):
    node_id: UUID
    node_type: str
    title: str
    summary: str | None
    importance: float
    confidence: float
    status: RunNodeStatus
    merged_into_node_id: UUID | None
    change_kind: RunGraphChangeKind


class RunGraphEdgeResponse(BaseModel):
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


class RunGraphResponse(BaseModel):
    run_id: UUID
    source_id: UUID
    run_status: Literal["pending", "processing", "completed", "failed"]
    nodes: list[RunGraphNodeResponse]
    edges: list[RunGraphEdgeResponse]


def _print_ingestion_result(run: IngestionRunRecord) -> None:
    """Temporarily expose extraction and resolution output in the console."""

    if run.status != "completed" or run.result is None:
        return
    proposed_graph = run.result.get("proposed_graph")
    transaction_plan = run.result.get("transaction_plan")
    if proposed_graph is None and transaction_plan is None:
        return
    output: list[str] = []
    if proposed_graph is not None:
        output.append(
            f"Proposed subgraph for ingestion run {run.id}:\n"
            f"{json.dumps(proposed_graph, indent=2, sort_keys=True)}"
        )
    if transaction_plan is not None:
        output.append(
            f"Resolution transaction plan for ingestion run {run.id}:\n"
            f"{json.dumps(transaction_plan, indent=2, sort_keys=True)}"
        )
    print("\n\n".join(output))


@router.post(
    "",
    response_model=IngestionRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ingestion(
    request: IngestionRequest,
    response: Response,
    _membership: WorkspaceMembership = Depends(require_workspace_writer),
    llm: ChunkExtractionLLM = Depends(get_extraction_llm),
    enrichment_llm: TypeSpecificEnrichmentLLM = Depends(get_enrichment_llm),
    resolver_store: CanonicalGraphResolverStore = Depends(get_resolver_store),
) -> IngestionRunResponse | JSONResponse:
    """Extract and resolve one source in the authorized workspace."""

    workspace_id = _membership.workspace_id
    try:
        run = await ingest_content(
            workspace_id=workspace_id,
            source_type=request.source_type,
            content=request.content,
            source_metadata=request.source_metadata,
            client_request_id=request.client_request_id,
            llm=llm,
            enrichment_llm=enrichment_llm,
            resolver_store=resolver_store,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IngestionExecutionError as exc:
        payload = IngestionRunResponse.from_record(exc.run)
        return JSONResponse(status_code=502, content=payload.model_dump(mode="json"))

    _print_ingestion_result(run)
    if run.status == "processing":
        response.status_code = status.HTTP_202_ACCEPTED
    return IngestionRunResponse.from_record(run)


@router.get("/{run_id}", response_model=IngestionRunResponse)
async def read_ingestion(
    run_id: UUID,
    _membership: WorkspaceMembership = Depends(require_workspace_member),
) -> IngestionRunResponse:
    run = get_ingestion_run(
        workspace_id=_membership.workspace_id,
        run_id=run_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found.")
    return IngestionRunResponse.from_record(run)


@router.get("/{run_id}/graph", response_model=RunGraphResponse)
async def read_ingestion_graph(
    run_id: UUID,
    _membership: WorkspaceMembership = Depends(require_workspace_member),
    run_graph_store: RunGraphStore = Depends(get_run_graph_store),
) -> RunGraphResponse:
    """Return every node and edge the run created, updated, archived, or merged.

    Rows reflect the graph's current state; ``change_kind`` says what the run
    did. Pending, processing, and failed runs return empty lists because a plan
    is applied atomically on completion.
    """

    graph = await run_graph_store.get_run_graph(
        workspace_id=_membership.workspace_id,
        run_id=run_id,
    )
    if graph is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found.")
    return RunGraphResponse.model_validate(asdict(graph))
