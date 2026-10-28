"""Ollama integration adapters."""

from .embeddings import (
    EmbeddingServiceClient,
    EmbeddingError,
    OllamaEmbeddingEngine,
)

__all__ = [
    "EmbeddingServiceClient",
    "EmbeddingError",
    "OllamaEmbeddingEngine",
]
