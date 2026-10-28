"""Store, LLM, and embedding providers injected into API routes.

Tests replace these through ``app.dependency_overrides``.
"""

from __future__ import annotations

from functools import lru_cache

from app.integrations.ollama import EmbeddingServiceClient
from app.graph.queries.edge_detail import EdgeDetailStore
from app.graph.queries.memory_surfacing import MemorySurfacingStore
from app.graph.queries.node_neighborhood import NodeNeighborhoodStore
from app.graph.queries.run_graph import RunGraphStore
from app.graph.queries.edge_detail.sql.postgres import PostgresEdgeDetailStore
from app.graph.queries.memory_surfacing.sql.postgres import (
    PostgresMemorySurfacingStore,
)
from app.graph.queries.node_neighborhood.sql.postgres import PostgresNodeNeighborhoodStore
from app.graph.queries.node_search.sql.postgres import PostgresNodeSearchRepository
from app.graph.queries.run_graph.sql.postgres import PostgresRunGraphStore
from app.graph.queries.node_search import NodeSearchEngine, NodeSearchStore
from app.workflows.ingest.enrichment.adapters.groq import (
    GroqTypeSpecificEnrichmentLLM,
)
from app.workflows.ingest.extraction.adapters.groq_two_stage import (
    GroqTwoStageExtractionLLM,
)
from app.workflows.ingest.extraction.service import ChunkExtractionLLM
from app.workflows.ingest.resolution.sql.postgres import (
    PostgresCanonicalGraphResolverStore,
    load_active_relation_types,
)


def get_extraction_llm() -> ChunkExtractionLLM:
    """Build the two-stage extractor from the active relation catalog."""

    relation_definitions = load_active_relation_types()
    return GroqTwoStageExtractionLLM(relation_definitions=relation_definitions)


@lru_cache(maxsize=1)
def get_enrichment_llm() -> GroqTypeSpecificEnrichmentLLM:
    """Return the second-pass type-specific field extractor."""

    return GroqTypeSpecificEnrichmentLLM()


@lru_cache(maxsize=1)
def get_resolver_store() -> PostgresCanonicalGraphResolverStore:
    """Return live workspace-scoped canonical graph reads."""

    return PostgresCanonicalGraphResolverStore(embedder=EmbeddingServiceClient())


@lru_cache(maxsize=1)
def get_node_search_store() -> NodeSearchStore:
    """Return client node search over lexical matches and projection vectors."""

    return NodeSearchEngine(
        repository=PostgresNodeSearchRepository(),
        embedder=EmbeddingServiceClient(),
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

    return EmbeddingServiceClient().model
