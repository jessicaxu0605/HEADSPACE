from __future__ import annotations

import os
import unittest
from collections.abc import AsyncIterable
from uuid import uuid4

from groq import AsyncGroq

from app.workflows.ingest.extraction.adapters.groq_chunk import (
    DEFAULT_GROQ_EXTRACTION_MODEL,
    GroqChunkExtractionLLM,
)
from app.workflows.ingest.extraction.service import extract_proposed_subgraph
from app.workflows.ingest.resolution.sql.postgres import load_active_relation_types
from app.workflows.ingest.models import (
    ExtractionSourceContext,
    PreparedSource,
    SourceChunk,
)


async def chunk_stream(*chunks: SourceChunk) -> AsyncIterable[SourceChunk]:
    for chunk in chunks:
        yield chunk


def prepared_source(*texts: str) -> PreparedSource:
    source_id = uuid4()
    return PreparedSource(
        source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
        chunks=chunk_stream(
            *(
                SourceChunk(f"chunk-{index}", source_id, text)
                for index, text in enumerate(texts, start=1)
            )
        ),
    )


@unittest.skipUnless(
    os.getenv("RUN_GROQ_INTEGRATION_TEST") == "1",
    "Set RUN_GROQ_INTEGRATION_TEST=1 to call the live Groq API.",
)
class GroqApiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_a_brief_answer(self) -> None:
        api_key = os.getenv("GROQ_API_KEY")
        self.assertTrue(api_key, "GROQ_API_KEY is not set")

        client = AsyncGroq(api_key=api_key, max_retries=2, timeout=30.0)
        response = await client.chat.completions.create(
            model=DEFAULT_GROQ_EXTRACTION_MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            reasoning_effort="low",
            max_completion_tokens=32,
        )

        answer = response.choices[0].message.content
        self.assertIsNotNone(answer)
        self.assertEqual(answer.strip(), "OK")

    async def test_extracts_one_chunk(self) -> None:
        source = prepared_source("Alice knows Bob.")
        result = await extract_proposed_subgraph(
            source,
            GroqChunkExtractionLLM(
                relation_definitions=load_active_relation_types(),
                max_completion_tokens=2_000,
            ),
            boundary_chars=0,
        )

        names = {
            name.casefold()
            for node in result.proposed_graph.nodes
            for name in node.names
        }
        self.assertIn("alice", names)
        self.assertIn("bob", names)
        self.assertGreaterEqual(len(result.proposed_graph.edges), 1)
        evidence_spans = [
            evidence.raw_content
            for edge in result.proposed_graph.edges
            for evidence in edge.evidence
        ]
        self.assertTrue(evidence_spans)
        self.assertTrue(
            all(span in "Alice knows Bob." for span in evidence_spans)
        )

    async def test_extracts_and_aggregates_two_chunks(self) -> None:
        chunk_texts = ("Alice knows Bob.", "Bob trusts Carol.")
        source = prepared_source(*chunk_texts)
        result = await extract_proposed_subgraph(
            source,
            GroqChunkExtractionLLM(
                relation_definitions=load_active_relation_types(),
                max_completion_tokens=2_000,
            ),
            boundary_chars=0,
        )

        names = {
            name.casefold()
            for node in result.proposed_graph.nodes
            for name in node.names
        }
        self.assertIn("alice", names)
        self.assertIn("bob", names)
        self.assertIn("carol", names)
        self.assertEqual(len(result.proposed_graph.nodes), 3)
        self.assertGreaterEqual(len(result.proposed_graph.edges), 2)

        evidence_spans = [
            evidence.raw_content
            for edge in result.proposed_graph.edges
            for evidence in edge.evidence
        ]
        normalized_evidence = [span.casefold() for span in evidence_spans]
        self.assertTrue(any("knows" in span for span in normalized_evidence))
        self.assertTrue(any("trusts" in span for span in normalized_evidence))


if __name__ == "__main__":
    unittest.main()
