from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.integrations.ollama import EmbeddingError, EmbeddingServiceClient


class RecordingTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object], float, str | None]] = []

    def __call__(self, url, payload, timeout_seconds, token):
        self.calls.append((url, dict(payload), timeout_seconds, token))
        return self.response


class EmbeddingServiceClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        environment = patch.dict(
            os.environ,
            {
                "EMBEDDING_SERVICE_URL": "http://127.0.0.1:8001",
                "EMBEDDING_MODEL": "nomic-embed-text",
                "EMBEDDING_DIMENSIONS": "2",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)

    async def test_embeds_documents_through_internal_service(self) -> None:
        transport = RecordingTransport(
            {"model": "nomic-embed-text", "embeddings": [[0.6, 0.8]]}
        )
        client = EmbeddingServiceClient(
            dimensions=2,
            base_url="http://embeddings.internal:8001/",
            token="secret",
            transport=transport,
        )

        vectors = await client.embed_documents(["  Sarah Chen  "])

        self.assertEqual(vectors, [[0.6, 0.8]])
        url, payload, timeout, token = transport.calls[0]
        self.assertEqual(url, "http://embeddings.internal:8001/v1/embeddings")
        self.assertEqual(payload, {"input": ["Sarah Chen"], "input_type": "document"})
        self.assertEqual(timeout, 30.0)
        self.assertEqual(token, "secret")

    async def test_embeds_query_as_query_input(self) -> None:
        transport = RecordingTransport(
            {"model": "nomic-embed-text", "embeddings": [[1.0, 0.0]]}
        )
        client = EmbeddingServiceClient(dimensions=2, transport=transport)

        self.assertEqual(await client.embed_query("Sarah"), [1.0, 0.0])
        self.assertEqual(transport.calls[0][1]["input_type"], "query")

    async def test_empty_document_batch_does_not_call_service(self) -> None:
        transport = RecordingTransport({})
        client = EmbeddingServiceClient(dimensions=2, transport=transport)

        self.assertEqual(await client.embed_documents([]), [])
        self.assertEqual(transport.calls, [])

    async def test_rejects_wrong_dimensions(self) -> None:
        client = EmbeddingServiceClient(
            dimensions=3,
            transport=RecordingTransport(
                {"model": "nomic-embed-text", "embeddings": [[1.0, 0.0]]}
            ),
        )

        with self.assertRaisesRegex(EmbeddingError, "3 dimensions"):
            await client.embed_query("Sarah")

    async def test_rejects_wrong_model(self) -> None:
        client = EmbeddingServiceClient(
            dimensions=2,
            transport=RecordingTransport(
                {"model": "other-model", "embeddings": [[1.0, 0.0]]}
            ),
        )

        with self.assertRaisesRegex(EmbeddingError, "other-model"):
            await client.embed_query("Sarah")

    async def test_rejects_non_normalized_vector(self) -> None:
        client = EmbeddingServiceClient(
            dimensions=2,
            transport=RecordingTransport(
                {"model": "nomic-embed-text", "embeddings": [[2.0, 0.0]]}
            ),
        )

        with self.assertRaisesRegex(EmbeddingError, "non-normalized"):
            await client.embed_query("Sarah")

    async def test_rejects_blank_input(self) -> None:
        client = EmbeddingServiceClient(
            dimensions=2, transport=RecordingTransport({})
        )

        with self.assertRaisesRegex(ValueError, "non-blank"):
            await client.embed_documents([" "])

    def test_reads_service_configuration_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_SERVICE_URL": "http://embeddings.internal:8001/",
                "EMBEDDING_SERVICE_TOKEN": "secret",
                "EMBEDDING_MODEL": "other-model",
                "EMBEDDING_DIMENSIONS": "2",
            },
            clear=True,
        ):
            client = EmbeddingServiceClient()

        self.assertEqual(client.base_url, "http://embeddings.internal:8001")
        self.assertEqual(client.token, "secret")
        self.assertEqual(client.model, "other-model")
        self.assertEqual(client.dimensions, 2)

    def test_requires_deployment_contract(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "EMBEDDING_SERVICE_URL"):
                EmbeddingServiceClient()


if __name__ == "__main__":
    unittest.main()
