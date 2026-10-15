"""Embedding providers used to build derived search projections."""

from app.embeddings.ollama import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    OllamaEmbeddingEngine,
)
from app.search.common import EmbeddingError

__all__ = [
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "DEFAULT_EMBEDDING_MODEL",
    "EmbeddingError",
    "OllamaEmbeddingEngine",
]
