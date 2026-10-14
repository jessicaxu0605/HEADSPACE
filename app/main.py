"""Public HTTP API for source ingestion and canonical node queries."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db import ensure_temporary_prototype_workspace
from app.workflows.extract_proposed_subgraph import ChunkExtractionLLM
from app.workflows.groq_chunk_extraction import GroqChunkExtractionLLM
from app.workflows.ingest import (
    IngestionExecutionError,
    get_ingestion_run,
    ingest_content,
)
from app.workflows.postgres_canonical_graph import PostgresCanonicalGraphResolverStore
from app.workflows.resolve_proposed_subgraph import (
    CanonicalGraphResolverStore,
    NodeMatchKind,
)
from app.workflows.types import (
    IngestionRunRecord,
    MAX_LOAD_ALL_BYTES,
    NodeType,
    ProposedExternalId,
    ProposedNode,
)


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
    node_type: NodeType
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
    match_kind: NodeMatchKind
    match_score: float
    reasons: list[str]


class NodeQueryResponse(BaseModel):
    query: str
    node_type: NodeType
    matches: list[NodeQueryMatchResponse]


@lru_cache(maxsize=1)
def get_extraction_llm() -> GroqChunkExtractionLLM:
    return GroqChunkExtractionLLM()


@lru_cache(maxsize=1)
def get_resolver_store() -> CanonicalGraphResolverStore:
    """Return live workspace-scoped canonical graph reads."""

    return PostgresCanonicalGraphResolverStore()


def _cors_origins() -> list[str]:
    configured = os.getenv("API_CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


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


app = FastAPI(
    title="Headspace API",
    version="0.1.0",
    description=(
        "Persists source content, extracts a chunk-aggregated proposed graph, "
        "atomically applies canonical graph changes, and queries canonical nodes."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/nodes/query",
    response_model=NodeQueryResponse,
    tags=["nodes"],
)
async def query_node(
    request: NodeQueryRequest,
    resolver_store: CanonicalGraphResolverStore = Depends(get_resolver_store),
) -> NodeQueryResponse:
    """Return canonical nodes ranked by the ingestion identity matcher."""

    # TEMPORARY: replace with the authenticated request's workspace ID before
    # this endpoint is used in a multi-user or production deployment.
    workspace_id = ensure_temporary_prototype_workspace()
    names = [request.query, *request.aliases]
    candidates = await resolver_store.find_node_candidates(
        workspace_id=workspace_id,
        proposed_node=ProposedNode(
            proposed_node_id="node-query",
            node_type=request.node_type,
            names=names,
            primary_reference_name=request.query,
            external_ids=[
                ProposedExternalId(item.provider, item.external_id)
                for item in request.external_ids
            ],
        ),
    )
    return NodeQueryResponse(
        query=request.query,
        node_type=request.node_type,
        matches=[
            NodeQueryMatchResponse(
                node_id=candidate.node.node_id,
                node_type=candidate.node.node_type,
                title=candidate.node.title,
                identifiers=candidate.node.identifiers,
                match_kind=candidate.match_kind,
                match_score=candidate.match_score,
                reasons=candidate.reasons,
            )
            for candidate in candidates[: request.limit]
        ],
    )


@app.post(
    "/api/v1/ingestions",
    response_model=IngestionRunResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["ingestion"],
)
async def create_ingestion(
    request: IngestionRequest,
    response: Response,
    llm: ChunkExtractionLLM = Depends(get_extraction_llm),
    resolver_store: CanonicalGraphResolverStore = Depends(get_resolver_store),
) -> IngestionRunResponse | JSONResponse:
    """Extract and resolve one source using temporary prototype identity routing."""

    # TEMPORARY: replace with the authenticated request's workspace ID before
    # this endpoint is used in a multi-user or production deployment.
    workspace_id = ensure_temporary_prototype_workspace()
    try:
        run = await ingest_content(
            workspace_id=workspace_id,
            source_type=request.source_type,
            content=request.content,
            source_metadata=request.source_metadata,
            client_request_id=request.client_request_id,
            llm=llm,
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


@app.get(
    "/api/v1/ingestions/{run_id}",
    response_model=IngestionRunResponse,
    tags=["ingestion"],
)
async def read_ingestion(run_id: UUID) -> IngestionRunResponse:
    run = get_ingestion_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found.")
    return IngestionRunResponse.from_record(run)
