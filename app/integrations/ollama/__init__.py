"""Ollama integration adapters."""

from .embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingError,
    OllamaEmbeddingEngine,
)

__all__ = [
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "DEFAULT_EMBEDDING_MODEL",
    "EmbeddingError",
    "OllamaEmbeddingEngine",
]
