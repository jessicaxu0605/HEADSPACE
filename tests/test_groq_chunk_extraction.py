from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.workflows.groq_chunk_extraction import (
    CHUNK_EXTRACTION_OUTPUT_SCHEMA,
    GroqChunkExtractionLLM,
    MalformedChunkExtractionResponseError,
    parse_chunk_extraction_output,
)
from app.workflows.types import (
    ChunkExtractionInput,
    ChunkExtractionOutput,
    ChunkExtractionRepairContext,
    ExtractionContext,
    ExtractionSourceContext,
    ExtractionValidationIssue,
    ProposedGraph,
    ProposedGraphHandoff,
    SourceChunk,
)


class FakeCompletions:
    def __init__(self, responses: list[str | None]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self.responses)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeClient:
    def __init__(self, responses: list[str | None]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def valid_wire_output() -> dict[str, object]:
    return {
        "chunk_subgraph": {
            "nodes": [
                {
                    "proposed_node_id": "n_alice",
                    "node_type": "person",
                    "names": ["Alice"],
                    "primary_reference_name": "Alice",
                    "summary": "A project lead",
                    "type_specific_json": json.dumps(
                        {"role": "lead", "active": True}
                    ),
                    "external_ids": [
                        {"provider": "test", "external_id": "alice-1"}
                    ],
                },
                {
                    "proposed_node_id": "n_bob",
                    "node_type": "person",
                    "names": ["Bob"],
                    "primary_reference_name": "Bob",
                    "summary": None,
                    "type_specific_json": None,
                    "external_ids": [],
                },
            ],
            "edges": [
                {
                    "proposed_edge_id": "e_knows",
                    "from_proposed_node_id": "n_alice",
                    "to_proposed_node_id": "n_bob",
                    "relation_type": "knows",
                    "evidence": [
                        {
                            "raw_content": "Alice knows Bob",
                            "confidence": 0.9,
                            "annotations": [
                                {"start": 12, "length": 3, "local_node_id": "n_bob"}
                            ],
                            "strength_boost": None,
                        }
                    ],
                }
            ],
        },
        "proposed_graph_handoff": {
            "nodes": [
                {
                    "proposed_node_id": "n_alice",
                    "node_type": "person",
                    "names": ["Alice"],
                    "primary_name": "Alice",
                    "handoff_aliases": ["she"],
                    "external_ids": [
                        {"provider": "test", "external_id": "alice-1"}
                    ],
                    "handoff_description": "Project lead",
                },
                {
                    "proposed_node_id": "n_bob",
                    "node_type": "person",
                    "names": ["Bob"],
                    "primary_name": "Bob",
                    "handoff_aliases": [],
                    "external_ids": [],
                    "handoff_description": None,
                },
            ],
            "edges": [
                {
                    "proposed_edge_id": "e_knows",
                    "from_proposed_node_id": "n_alice",
                    "to_proposed_node_id": "n_bob",
                    "relation_type": "knows",
                }
            ],
        },
        "updated_extraction_context": {
            "source_summary": "Alice knows Bob.",
            "current_topic": "Relationships",
            "speaker_context": None,
            "temporal_anchor": None,
            "unresolved_references": [],
            "extraction_notes": ["Synthetic fixture"],
        },
        "repair_items": [
            {
                "message": "Review ambiguous role",
                "item_type": "semantic_uncertainty",
                "details_json": json.dumps({"node_id": "n_alice"}),
            }
        ],
    }


def extraction_input() -> ChunkExtractionInput:
    source_id = uuid4()
    return ChunkExtractionInput(
        current_chunk=SourceChunk("c1", source_id, "Alice knows Bob"),
        source_context=ExtractionSourceContext(
            source_id, "manual_entry", {"fixture": True}
        ),
        extraction_context=ExtractionContext("Synthetic source"),
        proposed_graph_handoff=ProposedGraphHandoff(),
    )


class GroqChunkExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_wire_json_into_workflow_dataclasses(self) -> None:
        result = parse_chunk_extraction_output(json.dumps(valid_wire_output()))

        self.assertIsInstance(result, ChunkExtractionOutput)
        self.assertEqual(
            result.chunk_subgraph.nodes[0].type_specific,
            {"role": "lead", "active": True},
        )
        self.assertEqual(result.chunk_subgraph.edges[0].evidence[0].confidence, 0.9)
        self.assertEqual(result.repair_items[0].details, {"node_id": "n_alice"})

    async def test_sends_strict_schema_and_serializes_uuid_input(self) -> None:
        wire = json.dumps(valid_wire_output())
        fake_client = FakeClient([wire])
        adapter = GroqChunkExtractionLLM(client=fake_client)
        input_value = extraction_input()

        result = await adapter.extract_chunk(extraction_input=input_value)

        self.assertEqual(
            result.updated_extraction_context.current_topic,
            "Relationships",
        )
        call = fake_client.completions.calls[0]
        self.assertEqual(call["model"], "openai/gpt-oss-120b")
        self.assertEqual(call["reasoning_effort"], "low")
        response_format = call["response_format"]
        self.assertTrue(response_format["json_schema"]["strict"])
        user_message = call["messages"][1]["content"]
        self.assertIn(str(input_value.current_chunk.source_id), user_message)

    def test_configures_sdk_transport_retries(self) -> None:
        with patch(
            "app.workflows.groq_chunk_extraction.AsyncGroq"
        ) as async_groq:
            GroqChunkExtractionLLM(
                api_key="test-key",
                transport_retries=4,
                timeout_seconds=12.5,
            )

        async_groq.assert_called_once_with(
            api_key="test-key",
            max_retries=4,
            timeout=12.5,
        )

    def test_reads_api_key_from_local_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = os.path.join(directory, ".env.local")
            with open(env_path, "w", encoding="utf-8") as env_file:
                env_file.write("GROQ_API_KEY=local-test-key\n")

            with patch.dict(os.environ, {}, clear=True), patch(
                "app.workflows.groq_chunk_extraction.AsyncGroq"
            ) as async_groq:
                GroqChunkExtractionLLM(env_file=env_path)

        self.assertEqual(async_groq.call_args.kwargs["api_key"], "local-test-key")

    def test_process_environment_overrides_local_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = os.path.join(directory, ".env.local")
            with open(env_path, "w", encoding="utf-8") as env_file:
                env_file.write("GROQ_API_KEY=local-test-key\n")

            with patch.dict(
                os.environ, {"GROQ_API_KEY": "process-test-key"}, clear=True
            ), patch(
                "app.workflows.groq_chunk_extraction.AsyncGroq"
            ) as async_groq:
                GroqChunkExtractionLLM(env_file=env_path)

        self.assertEqual(async_groq.call_args.kwargs["api_key"], "process-test-key")

    async def test_includes_domain_repair_context_in_prompt(self) -> None:
        fake_client = FakeClient([json.dumps(valid_wire_output())])
        adapter = GroqChunkExtractionLLM(client=fake_client)
        repair_context = ChunkExtractionRepairContext(
            invalid_output=ChunkExtractionOutput(
                chunk_subgraph=ProposedGraph(),
                proposed_graph_handoff=ProposedGraphHandoff(),
                updated_extraction_context=ExtractionContext("Synthetic source"),
            ),
            issues=[
                ExtractionValidationIssue(
                    "evidence_not_in_chunk",
                    "chunk_subgraph.edges[0].evidence[0]",
                    "Evidence is not verbatim.",
                )
            ],
        )

        await adapter.extract_chunk(
            extraction_input=extraction_input(), repair_context=repair_context
        )

        prompt = fake_client.completions.calls[0]["messages"][1]["content"]
        self.assertIn("repair_domain_output", prompt)
        self.assertIn("evidence_not_in_chunk", prompt)
        self.assertIn("complete corrected ChunkExtractionOutput", prompt)

    async def test_repairs_malformed_embedded_json(self) -> None:
        invalid = valid_wire_output()
        invalid["chunk_subgraph"]["nodes"][0]["type_specific_json"] = "{bad"
        fake_client = FakeClient(
            [json.dumps(invalid), json.dumps(valid_wire_output())]
        )
        adapter = GroqChunkExtractionLLM(
            client=fake_client, response_repair_attempts=1
        )

        result = await adapter.extract_chunk(extraction_input=extraction_input())

        self.assertEqual(result.chunk_subgraph.nodes[0].type_specific["role"], "lead")
        self.assertEqual(len(fake_client.completions.calls), 2)
        repair_messages = fake_client.completions.calls[1]["messages"]
        self.assertIn("must contain valid JSON", repair_messages[-1]["content"])

    async def test_exposes_final_raw_response_after_repair_exhaustion(self) -> None:
        first = valid_wire_output()
        first["repair_items"][0]["details_json"] = "not-json"
        final = deepcopy(first)
        final["repair_items"][0]["details_json"] = "still-not-json"
        fake_client = FakeClient([json.dumps(first), json.dumps(final)])
        adapter = GroqChunkExtractionLLM(
            client=fake_client, response_repair_attempts=1
        )

        with self.assertRaises(MalformedChunkExtractionResponseError) as raised:
            await adapter.extract_chunk(extraction_input=extraction_input())

        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(raised.exception.raw_response, json.dumps(final))

    async def test_repairs_response_with_missing_content(self) -> None:
        fake_client = FakeClient([None, json.dumps(valid_wire_output())])
        adapter = GroqChunkExtractionLLM(
            client=fake_client, response_repair_attempts=1
        )

        result = await adapter.extract_chunk(extraction_input=extraction_input())

        self.assertEqual(result.chunk_subgraph.nodes[0].proposed_node_id, "n_alice")
        self.assertEqual(len(fake_client.completions.calls), 2)

    def test_strict_schema_closes_and_requires_every_object_property(self) -> None:
        def assert_strict(schema: object) -> None:
            if isinstance(schema, dict):
                schema_type = schema.get("type")
                if schema_type == "object":
                    self.assertFalse(schema["additionalProperties"])
                    self.assertEqual(
                        set(schema["required"]), set(schema["properties"])
                    )
                for value in schema.values():
                    assert_strict(value)
            elif isinstance(schema, list):
                for value in schema:
                    assert_strict(value)

        assert_strict(CHUNK_EXTRACTION_OUTPUT_SCHEMA)


if __name__ == "__main__":
    unittest.main()
