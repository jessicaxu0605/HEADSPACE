from __future__ import annotations

import math
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from services.embeddings.api import create_app
from services.embeddings.engine import EmbeddingError, OllamaEmbeddingEngine


class RecordingTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def __call__(self, url, payload, timeout_seconds):
        self.calls.append((url, dict(payload), timeout_seconds))
        return self.response


class EmbeddingEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        environment = patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
                "EMBEDDING_MODEL": "nomic-embed-text",
                "EMBEDDING_DIMENSIONS": "2",
                "EMBEDDING_DOCUMENT_PREFIX": "search_document: ",
                "EMBEDDING_QUERY_PREFIX": "search_query: ",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    async def test_applies_document_prefix_and_normalizes(self) -> None:
        transport = RecordingTransport({"embeddings": [[3.0, 4.0]]})
        engine = OllamaEmbeddingEngine(dimensions=2, transport=transport)

        vectors = await engine.embed_documents(["  Sarah Chen  "])

        self.assertEqual(vectors, [[0.6, 0.8]])
        self.assertEqual(
            transport.calls[0][1]["input"], ["search_document: Sarah Chen"]
        )

    async def test_applies_query_prefix(self) -> None:
        transport = RecordingTransport({"embeddings": [[1.0, 0.0]]})
        engine = OllamaEmbeddingEngine(dimensions=2, transport=transport)

        self.assertEqual(await engine.embed_query("Sarah"), [1.0, 0.0])
        self.assertEqual(transport.calls[0][1]["input"], ["search_query: Sarah"])

    async def test_rejects_invalid_vectors(self) -> None:
        engine = OllamaEmbeddingEngine(
            dimensions=2,
            transport=RecordingTransport({"embeddings": [[0.0, 0.0]]}),
        )

        with self.assertRaisesRegex(EmbeddingError, "zero-magnitude"):
            await engine.embed_query("Sarah")

    async def test_each_output_has_unit_length(self) -> None:
        engine = OllamaEmbeddingEngine(
            dimensions=2,
            transport=RecordingTransport(
                {"embeddings": [[2.0, 0.0], [1.0, 1.0]]}
            ),
        )

        vectors = await engine.embed_documents(["one", "two"])

        for vector in vectors:
            self.assertAlmostEqual(math.sqrt(sum(value**2 for value in vector)), 1.0)

    def test_requires_model_contract(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OLLAMA_BASE_URL"):
                OllamaEmbeddingEngine()


class StubEmbedder:
    model = "test-model"
    dimensions = 2

    async def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text):
        return [0.0, 1.0]


class EmbeddingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(embedder=StubEmbedder(), service_token="secret")
        )

    def test_health_reports_model_contract(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(
            response.json(),
            {"status": "ok", "model": "test-model", "dimensions": 2},
        )

    def test_embeds_documents_with_bearer_token(self) -> None:
        response = self.client.post(
            "/v1/embeddings",
            headers={"Authorization": "Bearer secret"},
            json={"input": ["one", "two"], "input_type": "document"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["embeddings"], [[1.0, 0.0], [1.0, 0.0]])

    def test_rejects_unauthorized_requests(self) -> None:
        response = self.client.post(
            "/v1/embeddings",
            json={"input": ["one"], "input_type": "document"},
        )

        self.assertEqual(response.status_code, 401)

    def test_query_accepts_exactly_one_input(self) -> None:
        response = self.client.post(
            "/v1/embeddings",
            headers={"Authorization": "Bearer secret"},
            json={"input": ["one", "two"], "input_type": "query"},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
