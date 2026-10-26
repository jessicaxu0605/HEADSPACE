from __future__ import annotations

import json
import os
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from groq import BadRequestError

from app.workflows.ingest.groq_chunk_extraction import (
    CHUNK_EXTRACTION_OUTPUT_SCHEMA,
    GroqChunkExtractionLLM,
    MalformedChunkExtractionResponseError,
    build_chunk_extraction_output_schema,
    parse_chunk_extraction_output,
)
from app.workflows.ingest.relation_types import RelationDefinition
from app.workflows.ingest.types import (
    ChunkExtractionInput,
    ChunkExtractionOutput,
    ChunkExtractionRepairContext,
    ExtractionContext,
    ExtractionSourceContext,
    ExtractionValidationIssue,
    ProposedEdge,
    ProposedEvidence,
    ProposedGraph,
    ProposedGraphHandoff,
    ProposedNode,
    SourceChunk,
)


TEST_RELATION_DEFINITIONS = [
    RelationDefinition(
        "related_to", "related_to", "General relationship.", is_symmetric=True
    ),
    RelationDefinition(
        "knows",
        "knows",
        "The two people know each other.",
        is_symmetric=True,
        allowed_from_types=("person",),
        allowed_to_types=("person",),
    ),
    RelationDefinition(
        "born_on",
        "birth_date_of",
        "The person's birthday.",
        is_exclusive=True,
        allowed_from_types=("person",),
        allowed_to_types=("date",),
    ),
    RelationDefinition(
        "possibly_same_as",
        "possibly_same_as",
        "System-only identity candidate.",
        is_symmetric=True,
        is_extractable=False,
    ),
]


class FakeCompletions:
    def __init__(self, responses: list[str | None]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self.responses)
        if isinstance(content, Exception):
            raise content
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
                    "details": "met through the climbing gym",
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
        self.assertEqual(
            result.chunk_subgraph.edges[0].details,
            "met through the climbing gym",
        )
        self.assertEqual(result.repair_items[0].details, {"node_id": "n_alice"})

    def test_edge_details_are_required_and_nullable(self) -> None:
        wire = valid_wire_output()
        wire["chunk_subgraph"]["edges"][0]["details"] = None
        result = parse_chunk_extraction_output(json.dumps(wire))
        self.assertIsNone(result.chunk_subgraph.edges[0].details)

        del wire["chunk_subgraph"]["edges"][0]["details"]
        with self.assertRaises(KeyError):
            parse_chunk_extraction_output(json.dumps(wire))

    def test_schema_and_prompt_request_edge_details(self) -> None:
        schema = build_chunk_extraction_output_schema(TEST_RELATION_DEFINITIONS)
        edge_schema = schema["properties"]["chunk_subgraph"]["properties"][
            "edges"
        ]["items"]
        self.assertIn("details", edge_schema["required"])
        self.assertEqual(edge_schema["properties"]["details"]["type"], ["string", "null"])
        self.assertNotIn("proposed_graph_handoff", schema["properties"])

    def test_normalizes_person_email_as_external_identity(self) -> None:
        wire = valid_wire_output()
        graph_node = wire["chunk_subgraph"]["nodes"][0]
        graph_node["names"] = ["andr3w.xu@gmail.com", "Andrew Xu"]
        graph_node["primary_reference_name"] = "andr3w.xu@gmail.com"
        graph_node["external_ids"] = []

        result = parse_chunk_extraction_output(json.dumps(wire))

        node = result.chunk_subgraph.nodes[0]
        self.assertEqual(node.primary_reference_name, "Andrew Xu")
        self.assertEqual(
            [(item.provider, item.external_id) for item in node.external_ids],
            [("email", "andr3w.xu@gmail.com")],
        )

    async def test_sends_strict_schema_and_serializes_uuid_input(self) -> None:
        wire = json.dumps(valid_wire_output())
        fake_client = FakeClient([wire])
        adapter = GroqChunkExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS,
            client=fake_client,
        )
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
            "app.workflows.ingest.groq_chunk_extraction.AsyncGroq"
        ) as async_groq:
            GroqChunkExtractionLLM(
                relation_definitions=TEST_RELATION_DEFINITIONS,
                api_key="test-key",
                transport_retries=4,
                timeout_seconds=12.5,
            )

        async_groq.assert_called_once_with(
            api_key="test-key",
            max_retries=4,
            timeout=12.5,
        )

    async def test_includes_domain_repair_context_in_prompt(self) -> None:
        fake_client = FakeClient([json.dumps(valid_wire_output())])
        adapter = GroqChunkExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS,
            client=fake_client,
        )
        repair_context = ChunkExtractionRepairContext(
            invalid_output=ChunkExtractionOutput(
                chunk_subgraph=ProposedGraph(
                    nodes=[ProposedNode("n1", "person", ["Unflagged"], "Unflagged")],
                    edges=[
                        ProposedEdge(
                            "e1",
                            "n1",
                            "n2",
                            "knows",
                            [ProposedEvidence("Flagged evidence", confidence=0.8)],
                        )
                    ],
                ),
                updated_extraction_context=ExtractionContext("Synthetic source"),
            ),
            issues=[
                ExtractionValidationIssue(
                    "missing_edge_endpoint",
                    "chunk_subgraph.edges[0]",
                    "Edge 'e1' refers to a missing proposed node.",
                )
            ],
        )

        await adapter.extract_chunk(
            extraction_input=extraction_input(), repair_context=repair_context
        )

        prompt = fake_client.completions.calls[0]["messages"][1]["content"]
        self.assertIn("repair_domain_output", prompt)
        self.assertIn("missing_edge_endpoint", prompt)
        self.assertIn("complete corrected ChunkExtractionOutput", prompt)
        self.assertIn("Flagged evidence", prompt)
        self.assertNotIn("Unflagged", prompt)

    async def test_repairs_malformed_embedded_json(self) -> None:
        invalid = valid_wire_output()
        invalid["chunk_subgraph"]["nodes"][0]["type_specific_json"] = "{bad"
        fake_client = FakeClient(
            [json.dumps(invalid), json.dumps(valid_wire_output())]
        )
        adapter = GroqChunkExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS,
            client=fake_client,
            response_repair_attempts=1,
        )

        result = await adapter.extract_chunk(extraction_input=extraction_input())

        self.assertEqual(result.chunk_subgraph.nodes[0].type_specific["role"], "lead")
        self.assertEqual(len(fake_client.completions.calls), 2)
        repair_messages = fake_client.completions.calls[1]["messages"]
        self.assertIn("must contain valid JSON", repair_messages[-1]["content"])
        self.assertEqual(
            [message["role"] for message in repair_messages],
            ["system", "user", "user"],
        )

    async def test_retries_groq_schema_validation_failure(self) -> None:
        rejected = BadRequestError(
            "Generated JSON does not match the expected schema.",
            response=httpx.Response(
                400, request=httpx.Request("POST", "https://api.groq.com")
            ),
            body={
                "error": {
                    "message": "relation_type must be one of 'knows'",
                    "code": "json_validate_failed",
                    "failed_generation": "{...}",
                }
            },
        )
        fake_client = FakeClient([rejected, json.dumps(valid_wire_output())])
        adapter = GroqChunkExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS,
            client=fake_client,
            response_repair_attempts=1,
        )

        result = await adapter.extract_chunk(extraction_input=extraction_input())

        self.assertEqual(result.chunk_subgraph.nodes[0].proposed_node_id, "n_alice")
        retry_messages = fake_client.completions.calls[1]["messages"]
        self.assertIn("relation_type must be one of", retry_messages[-1]["content"])
        self.assertNotIn("failed_generation", retry_messages[-1]["content"])

    async def test_exposes_final_raw_response_after_repair_exhaustion(self) -> None:
        first = valid_wire_output()
        first["repair_items"][0]["details_json"] = "not-json"
        final = deepcopy(first)
        final["repair_items"][0]["details_json"] = "still-not-json"
        fake_client = FakeClient([json.dumps(first), json.dumps(final)])
        adapter = GroqChunkExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS,
            client=fake_client,
            response_repair_attempts=1,
        )

        with self.assertRaises(MalformedChunkExtractionResponseError) as raised:
            await adapter.extract_chunk(extraction_input=extraction_input())

        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(raised.exception.raw_response, json.dumps(final))

    async def test_repairs_response_with_missing_content(self) -> None:
        fake_client = FakeClient([None, json.dumps(valid_wire_output())])
        adapter = GroqChunkExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS,
            client=fake_client,
            response_repair_attempts=1,
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

    def test_relation_types_are_constrained_to_the_fixed_catalog(self) -> None:
        schema = build_chunk_extraction_output_schema(TEST_RELATION_DEFINITIONS)
        edge_relation_schema = schema["properties"][
            "chunk_subgraph"
        ]["properties"]["edges"]["items"]["properties"]["relation_type"]

        self.assertEqual(edge_relation_schema["enum"], [
            "related_to", "knows", "born_on", "birth_date_of"
        ])
        self.assertNotIn("possibly_same_as", edge_relation_schema["enum"])
        self.assertNotIn("is_best_friends_with", edge_relation_schema["enum"])

    def test_schema_supports_organization_event_and_document_nodes(self) -> None:
        graph_node_types = CHUNK_EXTRACTION_OUTPUT_SCHEMA["properties"][
            "chunk_subgraph"
        ]["properties"]["nodes"]["items"]["properties"]["node_type"]["enum"]

        for node_type in ("organization", "event", "document"):
            self.assertIn(node_type, graph_node_types)


if __name__ == "__main__":
    unittest.main()
