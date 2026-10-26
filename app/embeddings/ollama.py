"""Local embedding engine backed by Ollama's native HTTP API."""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.search.common import EmbeddingError


DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_DIMENSIONS = 768
DEFAULT_DOCUMENT_PREFIX = "search_document: "
DEFAULT_QUERY_PREFIX = "search_query: "


Transport = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]


class OllamaEmbeddingEngine:
    """Generate normalized document and query vectors with a local Ollama model.

    The document/query distinction is explicit because the default Nomic model
    was trained with different prefixes for indexed content and search queries.
    Keeping that policy in this adapter prevents callers from silently producing
    vectors in mismatched spaces.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout_seconds: float = 30.0,
        document_prefix: str | None = None,
        query_prefix: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or _setting("OLLAMA_BASE_URL")
            or DEFAULT_OLLAMA_BASE_URL
        ).rstrip("/")
        self.model = (
            model
            or _setting("EMBEDDING_MODEL")
            or DEFAULT_EMBEDDING_MODEL
        )
        configured_dimensions = (
            dimensions
            if dimensions is not None
            else int(
                _setting("EMBEDDING_DIMENSIONS")
                or str(DEFAULT_EMBEDDING_DIMENSIONS)
            )
        )
        if configured_dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self.dimensions = configured_dimensions
        self.timeout_seconds = timeout_seconds
        self.document_prefix = (
            document_prefix
            if document_prefix is not None
            else _setting("EMBEDDING_DOCUMENT_PREFIX")
            or DEFAULT_DOCUMENT_PREFIX
        )
        self.query_prefix = (
            query_prefix
            if query_prefix is not None
            else _setting("EMBEDDING_QUERY_PREFIX")
            or DEFAULT_QUERY_PREFIX
        )
        self._transport = transport or _post_json

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed projection text for storage and cosine-distance search."""

        return await self._embed(texts, prefix=self.document_prefix)

    async def embed_query(self, text: str) -> list[float]:
        """Embed one search query in the model's query vector space."""

        vectors = await self._embed([text], prefix=self.query_prefix)
        return vectors[0]

    async def _embed(
        self, texts: Sequence[str], *, prefix: str
    ) -> list[list[float]]:
        input_texts = list(texts)
        if not input_texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in input_texts):
            raise ValueError("embedding input must contain non-blank strings")

        payload = {
            "model": self.model,
            "input": [f"{prefix}{text.strip()}" for text in input_texts],
        }
        try:
            response = await asyncio.to_thread(
                self._transport,
                f"{self.base_url}/api/embed",
                payload,
                self.timeout_seconds,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Ollama embedding request failed: {exc}") from exc

        raw_embeddings = response.get("embeddings")
        if not isinstance(raw_embeddings, list):
            raise EmbeddingError("Ollama response did not contain an embeddings list")
        if len(raw_embeddings) != len(input_texts):
            raise EmbeddingError(
                "Ollama returned a different number of embeddings than inputs"
            )

        return [
            _validate_and_normalize(vector, dimensions=self.dimensions)
            for vector in raw_embeddings
        ]


def _setting(name: str) -> str | None:
    return os.getenv(name)


def _validate_and_normalize(value: Any, *, dimensions: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimensions:
        actual = len(value) if isinstance(value, list) else "non-list"
        raise EmbeddingError(
            f"Expected an embedding with {dimensions} dimensions; received {actual}"
        )

    vector: list[float] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise EmbeddingError("Embedding contained a non-numeric component")
        number = float(component)
        if not math.isfinite(number):
            raise EmbeddingError("Embedding contained a non-finite component")
        vector.append(number)

    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0:
        raise EmbeddingError("Ollama returned a zero-magnitude embedding")
    return [component / magnitude for component in vector]


def _post_json(
    url: str, payload: Mapping[str, Any], timeout_seconds: float
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            result = json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise EmbeddingError(
            f"Ollama returned HTTP {exc.code}: {details or exc.reason}"
        ) from exc
    except URLError as exc:
        raise EmbeddingError(
            f"Cannot reach Ollama at {url}; start it with `ollama serve`: {exc.reason}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EmbeddingError("Ollama returned an invalid JSON response") from exc

    if not isinstance(result, dict):
        raise EmbeddingError("Ollama returned a non-object JSON response")
    return result
