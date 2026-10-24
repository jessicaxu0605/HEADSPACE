"""Public HTTP API for source ingestion and canonical node queries."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.api import router as auth_router
from app.embeddings import OllamaEmbeddingEngine
from app.graph.queries.edge_detail import EdgeDetailStore, EdgeStatus
from app.graph.queries.memory_surfacing import (
    AnchorKind,
    EdgeRole,
    MemorySurfacingStore,
    SurfacedMemory,
    SurfacingContext,
    edge_sentence,
)
from app.graph.queries.node_neighborhood import NeighborhoodNode, NodeNeighborhoodStore
from app.graph.queries.run_graph import (
    RunGraphChangeKind,
    RunGraphStore,
    RunNodeStatus,
)
from app.persistence.postgres.canonical_graph import (
    PostgresCanonicalGraphResolverStore,
    load_active_relation_types,
)
from app.persistence.postgres.connection import (
    DEFAULT_DEVELOPMENT_WORKSPACE_ID,
    close_async_pool,
    open_async_pool,
)
from app.persistence.postgres.edge_detail import PostgresEdgeDetailStore
from app.persistence.postgres.memory_surfacing import (
    DEFAULT_LANDING_SOURCES,
    PostgresMemorySurfacingStore,
)
from app.persistence.postgres.node_neighborhood import PostgresNodeNeighborhoodStore
from app.persistence.postgres.node_search import PostgresNodeSearchRepository
from app.persistence.postgres.run_graph import PostgresRunGraphStore
from app.workflows.ingest.enrich_type_specific import TypeSpecificEnrichmentLLM
from app.workflows.ingest.extract_proposed_subgraph import ChunkExtractionLLM
from app.workflows.ingest.groq_chunk_extraction import GroqChunkExtractionLLM
from app.workflows.ingest.groq_type_specific_enrichment import (
    GroqTypeSpecificEnrichmentLLM,
)
from app.search.node_search import (
    NodeSearchEngine,
    NodeSearchMatchKind,
    NodeSearchStore,
)
from app.workflows.ingest.resolve_proposed_subgraph import CanonicalGraphResolverStore
from app.workflows.ingest.type_specific import displayable_type_specific
from app.workflows.ingest.types import (
    IngestionRunRecord,
    MAX_LOAD_ALL_BYTES,
    NodeType,
    ProposedExternalId,
)
from app.workflows.ingest.workflow import (
    IngestionExecutionError,
    get_ingestion_run,
    ingest_content,
)
from app.workspaces.api import router as workspace_router


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


def _node_details(
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
            details=_node_details(node.node_type, type_specific),
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
                    details=_node_details(node.node_type, node.type_specific),
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


class RunGraphResponse(BaseModel):
    run_id: UUID
    source_id: UUID
    run_status: Literal["pending", "processing", "completed", "failed"]
    nodes: list[RunGraphNodeResponse]
    edges: list[RunGraphEdgeResponse]


def get_extraction_llm() -> GroqChunkExtractionLLM:
    """Build extraction from the current active PostgreSQL relation catalog."""

    return GroqChunkExtractionLLM(
        relation_definitions=load_active_relation_types()
    )


@lru_cache(maxsize=1)
def get_enrichment_llm() -> GroqTypeSpecificEnrichmentLLM:
    """Return the second-pass type-specific field extractor."""

    return GroqTypeSpecificEnrichmentLLM()


@lru_cache(maxsize=1)
def get_resolver_store() -> PostgresCanonicalGraphResolverStore:
    """Return live workspace-scoped canonical graph reads."""

    return PostgresCanonicalGraphResolverStore(embedder=OllamaEmbeddingEngine())


@lru_cache(maxsize=1)
def get_node_search_store() -> NodeSearchStore:
    """Return client node search over lexical matches and projection vectors."""

    return NodeSearchEngine(
        repository=PostgresNodeSearchRepository(),
        embedder=OllamaEmbeddingEngine(),
    )


@lru_cache(maxsize=1)
def get_neighborhood_store() -> NodeNeighborhoodStore:
    """Return live workspace-scoped graph traversal reads."""

    return PostgresNodeNeighborhoodStore()


@lru_cache(maxsize=1)
def get_edge_detail_store() -> EdgeDetailStore:
    """Return live workspace-scoped single-edge reads."""

    return PostgresEdgeDetailStore()


@lru_cache(maxsize=1)
def get_run_graph_store() -> RunGraphStore:
    """Return live workspace-scoped reads of per-run graph changes."""

    return PostgresRunGraphStore()


@lru_cache(maxsize=1)
def get_memory_surfacing_store() -> MemorySurfacingStore:
    """Return landing-page surfacing over the canonical graph."""

    return PostgresMemorySurfacingStore()


@lru_cache(maxsize=1)
def get_embedding_model() -> str:
    """Embedding model whose projections related-edge expansion reads."""

    return OllamaEmbeddingEngine().model


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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await open_async_pool()
    try:
        yield
    finally:
        await close_async_pool()


app = FastAPI(
    title="Headspace API",
    version="0.1.0",
    description=(
        "Persists source content, extracts a chunk-aggregated proposed graph, "
        "atomically applies canonical graph changes, and queries canonical nodes."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(auth_router)
app.include_router(workspace_router)


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
    search_store: NodeSearchStore = Depends(get_node_search_store),
) -> NodeQueryResponse:
    """Return canonical nodes related to the query, by keyword and meaning."""

    workspace_id = DEFAULT_DEVELOPMENT_WORKSPACE_ID
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


@app.get(
    "/api/v1/nodes/{node_id}/graph",
    response_model=NodeNeighborhoodResponse,
    tags=["nodes"],
)
async def read_node_neighborhood(
    node_id: UUID,
    depth: int = Query(default=1, ge=0, le=10),
    neighborhood_store: NodeNeighborhoodStore = Depends(get_neighborhood_store),
) -> NodeNeighborhoodResponse:
    """Return the active canonical graph within ``depth`` hops of a node."""

    workspace_id = DEFAULT_DEVELOPMENT_WORKSPACE_ID
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


@app.get(
    "/api/v1/edges/{edge_id}",
    response_model=EdgeDetailResponse,
    tags=["edges"],
)
async def read_edge(
    edge_id: UUID,
    edge_store: EdgeDetailStore = Depends(get_edge_detail_store),
) -> EdgeDetailResponse:
    """Return an edge with both endpoint nodes and all supporting evidence.

    Archived edges are still returned, with their status, so links to them keep
    working.
    """

    edge = await edge_store.get_edge_detail(
        workspace_id=DEFAULT_DEVELOPMENT_WORKSPACE_ID,
        edge_id=edge_id,
    )
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found.")
    return EdgeDetailResponse.model_validate(asdict(edge))


@app.get(
    "/api/v1/landing",
    response_model=LandingResponse,
    tags=["landing"],
)
async def read_landing(
    max_cards: int = Query(default=6, ge=0, le=20),
    max_upcoming: int = Query(default=8, ge=0, le=50),
    timezone: str = Query(
        default="UTC",
        description="IANA time zone used for today, anniversaries, and due dates.",
    ),
    exclude_node_ids: list[UUID] = Query(default=[]),
    exclude_edge_ids: list[UUID] = Query(default=[]),
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
        workspace_id=DEFAULT_DEVELOPMENT_WORKSPACE_ID,
        now=datetime.now(UTC),
        timezone=timezone,
        exclude_node_ids=frozenset(exclude_node_ids),
        exclude_edge_ids=frozenset(exclude_edge_ids),
        embedding_model=embedding_model,
    )
    upcoming = await surfacing_store.surface_upcoming(ctx=ctx, limit=max_upcoming)
    # Facts already in the upcoming list are not drawn again as memories.
    upcoming_edge_ids = {edge.edge_id for item in upcoming for edge in item.edges}
    upcoming_node_ids = {item.anchor.id for item in upcoming}
    memories = await surfacing_store.surface_memories(
        ctx=replace(
            ctx,
            exclude_node_ids=ctx.exclude_node_ids | upcoming_node_ids,
            exclude_edge_ids=ctx.exclude_edge_ids | upcoming_edge_ids,
        ),
        sources=DEFAULT_LANDING_SOURCES,
        max_cards=max_cards,
    )
    return LandingResponse(
        generated_at=ctx.now,
        timezone=timezone,
        upcoming=[LandingMemoryResponse.from_memory(item) for item in upcoming],
        memories=[LandingMemoryResponse.from_memory(memory) for memory in memories],
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
    enrichment_llm: TypeSpecificEnrichmentLLM = Depends(get_enrichment_llm),
    resolver_store: CanonicalGraphResolverStore = Depends(get_resolver_store),
) -> IngestionRunResponse | JSONResponse:
    """Extract and resolve one source in the default development workspace."""

    workspace_id = DEFAULT_DEVELOPMENT_WORKSPACE_ID
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


@app.get(
    "/api/v1/ingestions/{run_id}",
    response_model=IngestionRunResponse,
    tags=["ingestion"],
)
async def read_ingestion(run_id: UUID) -> IngestionRunResponse:
    run = get_ingestion_run(
        workspace_id=DEFAULT_DEVELOPMENT_WORKSPACE_ID,
        run_id=run_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found.")
    return IngestionRunResponse.from_record(run)


@app.get(
    "/api/v1/ingestions/{run_id}/graph",
    response_model=RunGraphResponse,
    tags=["ingestion"],
)
async def read_ingestion_graph(
    run_id: UUID,
    run_graph_store: RunGraphStore = Depends(get_run_graph_store),
) -> RunGraphResponse:
    """Return every node and edge the run created, updated, archived, or merged.

    Rows reflect the graph's current state; ``change_kind`` says what the run
    did. Pending, processing, and failed runs return empty lists because a plan
    is applied atomically on completion.
    """

    graph = await run_graph_store.get_run_graph(
        workspace_id=DEFAULT_DEVELOPMENT_WORKSPACE_ID,
        run_id=run_id,
    )
    if graph is None:
        raise HTTPException(status_code=404, detail="Ingestion run not found.")
    return RunGraphResponse.model_validate(asdict(graph))
