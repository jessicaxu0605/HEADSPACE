"""Store, LLM, and embedding providers injected into API routes.

Tests replace these through ``app.dependency_overrides``.
"""

from __future__ import annotations

import os
from functools import lru_cache

from app.embeddings import OllamaEmbeddingEngine
from app.graph.queries.edge_detail import EdgeDetailStore
from app.graph.queries.memory_surfacing import MemorySurfacingStore
from app.graph.queries.node_neighborhood import NodeNeighborhoodStore
from app.graph.queries.run_graph import RunGraphStore
from app.persistence.postgres.canonical_graph import (
    PostgresCanonicalGraphResolverStore,
    load_active_relation_types,
)
from app.persistence.postgres.edge_detail import PostgresEdgeDetailStore
from app.persistence.postgres.memory_surfacing import (
    PostgresMemorySurfacingStore,
)
from app.persistence.postgres.node_neighborhood import PostgresNodeNeighborhoodStore
from app.persistence.postgres.node_search import PostgresNodeSearchRepository
from app.persistence.postgres.run_graph import PostgresRunGraphStore
from app.search.node_search import NodeSearchEngine, NodeSearchStore
from app.workflows.ingest.extract_proposed_subgraph import ChunkExtractionLLM
from app.workflows.ingest.groq_chunk_extraction import GroqChunkExtractionLLM
from app.workflows.ingest.groq_two_stage_extraction import GroqTwoStageExtractionLLM
from app.workflows.ingest.groq_type_specific_enrichment import (
    GroqTypeSpecificEnrichmentLLM,
)


def get_extraction_llm() -> ChunkExtractionLLM:
    """Build extraction from the current active PostgreSQL relation catalog.

    INGEST_EXTRACTION_MODE=two_stage switches to separate node and edge calls
    while the two prompt designs are compared.
    """

    relation_definitions = load_active_relation_types()
    if os.getenv("INGEST_EXTRACTION_MODE") == "two_stage":
        return GroqTwoStageExtractionLLM(relation_definitions=relation_definitions)
    return GroqChunkExtractionLLM(relation_definitions=relation_definitions)


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
