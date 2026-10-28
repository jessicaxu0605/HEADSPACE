"""Internal HTTP service for document and query embeddings."""

from .api import create_app
from .engine import EmbeddingError, OllamaEmbeddingEngine

__all__ = ["EmbeddingError", "OllamaEmbeddingEngine", "create_app"]
