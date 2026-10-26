from __future__ import annotations

import math
import os
import unittest
from unittest.mock import patch

from app.embeddings import EmbeddingError, OllamaEmbeddingEngine


class RecordingTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def __call__(self, url, payload, timeout_seconds):
        self.calls.append((url, dict(payload), timeout_seconds))
        return self.response


class OllamaEmbeddingEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_embeds_documents_with_prefix_and_normalizes_vectors(self) -> None:
        transport = RecordingTransport({"embeddings": [[3.0, 4.0]]})
        engine = OllamaEmbeddingEngine(
            dimensions=2,
            base_url="http://localhost:11434/",
            transport=transport,
        )

        vectors = await engine.embed_documents(["  Sarah Chen  "])

        self.assertEqual(vectors, [[0.6, 0.8]])
        url, payload, timeout = transport.calls[0]
        self.assertEqual(url, "http://localhost:11434/api/embed")
        self.assertEqual(payload["model"], "nomic-embed-text")
        self.assertEqual(payload["input"], ["search_document: Sarah Chen"])
        self.assertEqual(timeout, 30.0)

    async def test_embeds_query_with_query_prefix(self) -> None:
        transport = RecordingTransport({"embeddings": [[1.0, 0.0]]})
        engine = OllamaEmbeddingEngine(dimensions=2, transport=transport)

        vector = await engine.embed_query("Sarah")

        self.assertEqual(vector, [1.0, 0.0])
        self.assertEqual(
            transport.calls[0][1]["input"], ["search_query: Sarah"]
        )

    async def test_empty_document_batch_does_not_call_ollama(self) -> None:
        transport = RecordingTransport({"embeddings": []})
        engine = OllamaEmbeddingEngine(dimensions=2, transport=transport)

        self.assertEqual(await engine.embed_documents([]), [])
        self.assertEqual(transport.calls, [])

    async def test_rejects_wrong_dimensions(self) -> None:
        engine = OllamaEmbeddingEngine(
            dimensions=3,
            transport=RecordingTransport({"embeddings": [[1.0, 0.0]]}),
        )

        with self.assertRaisesRegex(EmbeddingError, "3 dimensions"):
            await engine.embed_query("Sarah")

    async def test_rejects_zero_vector(self) -> None:
        engine = OllamaEmbeddingEngine(
            dimensions=2,
            transport=RecordingTransport({"embeddings": [[0.0, 0.0]]}),
        )

        with self.assertRaisesRegex(EmbeddingError, "zero-magnitude"):
            await engine.embed_query("Sarah")

    async def test_rejects_blank_input(self) -> None:
        engine = OllamaEmbeddingEngine(
            dimensions=2,
            transport=RecordingTransport({"embeddings": []}),
        )

        with self.assertRaisesRegex(ValueError, "non-blank"):
            await engine.embed_documents([" "])

    def test_reads_provider_configuration_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OLLAMA_BASE_URL": "http://ollama.internal:11434/",
                "EMBEDDING_MODEL": "other-model",
                "EMBEDDING_DIMENSIONS": "2",
                "EMBEDDING_DOCUMENT_PREFIX": "doc: ",
                "EMBEDDING_QUERY_PREFIX": "query: ",
            },
            clear=True,
        ):
            engine = OllamaEmbeddingEngine()

        self.assertEqual(engine.base_url, "http://ollama.internal:11434")
        self.assertEqual(engine.model, "other-model")
        self.assertEqual(engine.dimensions, 2)
        self.assertEqual(engine.document_prefix, "doc: ")
        self.assertEqual(engine.query_prefix, "query: ")

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


if __name__ == "__main__":
    unittest.main()
