"""Client for the standalone Headspace embedding service."""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmbeddingError(RuntimeError):
    """Raised when the embedding service cannot produce a valid vector."""


Transport = Callable[
    [str, Mapping[str, Any], float, str | None], Mapping[str, Any]
]


class EmbeddingServiceClient:
    """Generate vectors through the separately deployed internal service."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout_seconds: float = 30.0,
        token: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = (
            base_url
            if base_url is not None
            else _required_setting("EMBEDDING_SERVICE_URL")
        ).rstrip("/")
        self.model = (
            model if model is not None else _required_setting("EMBEDDING_MODEL")
        )
        self.dimensions = (
            dimensions
            if dimensions is not None
            else int(_required_setting("EMBEDDING_DIMENSIONS"))
        )
        if self.dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self.timeout_seconds = timeout_seconds
        self.token = (
            token if token is not None else os.getenv("EMBEDDING_SERVICE_TOKEN")
        )
        self._transport = transport or _post_json

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed projection text for storage and cosine-distance search."""

        return await self._embed(texts, input_type="document")

    async def embed_query(self, text: str) -> list[float]:
        """Embed one search query in the model's query vector space."""

        vectors = await self._embed([text], input_type="query")
        return vectors[0]

    async def _embed(
        self, texts: Sequence[str], *, input_type: str
    ) -> list[list[float]]:
        input_texts = list(texts)
        if not input_texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in input_texts):
            raise ValueError("embedding input must contain non-blank strings")

        payload = {
            "input": [text.strip() for text in input_texts],
            "input_type": input_type,
        }
        try:
            response = await asyncio.to_thread(
                self._transport,
                f"{self.base_url}/v1/embeddings",
                payload,
                self.timeout_seconds,
                self.token,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Embedding service request failed: {exc}") from exc

        response_model = response.get("model")
        if response_model != self.model:
            raise EmbeddingError(
                "Embedding service used model "
                f"{response_model!r}; expected {self.model!r}"
            )
        raw_embeddings = response.get("embeddings")
        if not isinstance(raw_embeddings, list):
            raise EmbeddingError(
                "Embedding service response did not contain an embeddings list"
            )
        if len(raw_embeddings) != len(input_texts):
            raise EmbeddingError(
                "Embedding service returned a different number of embeddings "
                "than inputs"
            )
        return [
            _validate_vector(vector, dimensions=self.dimensions)
            for vector in raw_embeddings
        ]


# Temporary compatibility alias for callers outside this repository.
OllamaEmbeddingEngine = EmbeddingServiceClient


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required")
    return value


def _validate_vector(value: Any, *, dimensions: int) -> list[float]:
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
    if not math.isclose(magnitude, 1.0, rel_tol=1e-5, abs_tol=1e-6):
        raise EmbeddingError("Embedding service returned a non-normalized vector")
    return vector


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
    token: str | None,
) -> Mapping[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            result = json.load(response)
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise EmbeddingError(
            f"Embedding service returned HTTP {exc.code}: {details or exc.reason}"
        ) from exc
    except URLError as exc:
        raise EmbeddingError(
            f"Cannot reach embedding service at {url}: {exc.reason}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EmbeddingError("Embedding service returned invalid JSON") from exc

    if not isinstance(result, dict):
        raise EmbeddingError("Embedding service returned non-object JSON")
    return result
