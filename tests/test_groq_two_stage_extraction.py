from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.workflows.ingest.extraction.adapters import groq_two_stage
from app.workflows.ingest.extraction.adapters.groq_two_stage import (
    GroqTwoStageExtractionLLM,
)
from app.workflows.ingest.models import (
    ChunkExtractionInput,
    ChunkExtractionRepairContext,
    ExtractionContext,
    ExtractionSourceContext,
    ExtractionValidationIssue,
    ProposedGraphHandoff,
    SourceChunk,
)
from tests.test_groq_chunk_extraction import (
    TEST_RELATION_DEFINITIONS,
    FakeClient,
)


def node_wire(node_id: str, node_type: str, name: str) -> dict[str, object]:
    return {
        "proposed_node_id": node_id,
        "node_type": node_type,
        "names": [name],
        "primary_reference_name": name,
        "summary": None,
        "type_specific_json": None,
        "external_ids": [],
    }


NODE_RESPONSE = json.dumps(
    {
        "nodes": [
            node_wire("alice", "person", "Alice"),
            node_wire("bob", "person", "Bob"),
            node_wire("may3", "date", "May 3"),
        ],
        "updated_extraction_context": {
            "source_summary": "Alice and Bob.",
            "current_topic": None,
            "speaker_context": None,
            "temporal_anchor": None,
            "unresolved_references": [],
            "extraction_notes": [],
        },
        "repair_items": [],
    }
)
EDGE_RESPONSE = json.dumps(
    {
        "edges": [
            {
                "proposed_edge_id": "e1",
                "from_proposed_node_id": "alice",
                "to_proposed_node_id": "bob",
                "relation_type": "knows",
                "evidence": [
                    {
                        "raw_content": "Alice knows Bob",
                        "confidence": 0.9,
                        "annotations": [],
                        "strength_boost": None,
                    }
                ],
                "details": None,
                "preferred_relation": None,
            }
        ]
    }
)


def extraction_input() -> ChunkExtractionInput:
    source_id = uuid4()
    return ChunkExtractionInput(
        current_chunk=SourceChunk("c1", source_id, "Alice knows Bob since May 3."),
        source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
        extraction_context=ExtractionContext("Nothing yet."),
        proposed_graph_handoff=ProposedGraphHandoff(),
    )


class GroqTwoStageExtractionTests(unittest.IsolatedAsyncioTestCase):
    def llm(self, client: FakeClient) -> GroqTwoStageExtractionLLM:
        return GroqTwoStageExtractionLLM(
            relation_definitions=TEST_RELATION_DEFINITIONS, client=client
        )

    async def test_combines_node_and_edge_stages(self) -> None:
        client = FakeClient([NODE_RESPONSE, EDGE_RESPONSE])

        output = await self.llm(client).extract_chunk(
            extraction_input=extraction_input()
        )

        self.assertEqual(
            [node.proposed_node_id for node in output.chunk_subgraph.nodes],
            ["alice", "bob", "may3"],
        )
        self.assertEqual(output.chunk_subgraph.edges[0].relation_type, "knows")
        self.assertEqual(output.updated_extraction_context.source_summary, "Alice and Bob.")

    async def test_edge_stage_is_constrained_to_known_nodes_and_valid_pairs(
        self,
    ) -> None:
        client = FakeClient([NODE_RESPONSE, EDGE_RESPONSE])

        await self.llm(client).extract_chunk(extraction_input=extraction_input())

        self.assertEqual(
            [call["temperature"] for call in client.completions.calls], [0.5, 0.5]
        )
        edge_call = client.completions.calls[1]
        edge_items = edge_call["response_format"]["json_schema"]["schema"][
            "properties"
        ]["edges"]["items"]["properties"]
        self.assertEqual(
            edge_items["from_proposed_node_id"]["enum"], ["alice", "bob", "may3"]
        )
        self.assertNotIn(
            "possibly_same_as", edge_items["relation_type"]["enum"]
        )
        payload = json.loads(
            edge_call["messages"][1]["content"].split("\n\n", 1)[1]
        )
        self.assertEqual(payload["relations_valid_for_any_pair"], ["related_to"])
        pairs = payload["specific_relations_by_type_pair"]
        self.assertEqual(pairs["person -> date"], ["born_on"])
        self.assertEqual(pairs["date -> person"], ["birth_date_of"])
        self.assertNotIn("date -> date", pairs)

    async def test_edge_only_repair_reuses_nodes(self) -> None:
        client = FakeClient([NODE_RESPONSE, EDGE_RESPONSE, EDGE_RESPONSE])
        llm = self.llm(client)
        first = await llm.extract_chunk(extraction_input=extraction_input())

        repaired = await llm.extract_chunk(
            extraction_input=extraction_input(),
            repair_context=ChunkExtractionRepairContext(
                invalid_output=first,
                issues=[
                    ExtractionValidationIssue(
                        "invalid_relation_endpoints",
                        "chunk_subgraph.edges[0].relation_type",
                        "Wrong relation.",
                    )
                ],
            ),
        )

        self.assertEqual(len(client.completions.calls), 3)
        self.assertEqual(
            client.completions.calls[2]["response_format"]["json_schema"]["name"],
            "edge_extraction_output",
        )
        self.assertIs(repaired.chunk_subgraph.nodes, first.chunk_subgraph.nodes)

    async def test_records_preferred_relation_as_catalog_gap(self) -> None:
        edge_response = json.loads(EDGE_RESPONSE)
        edge = edge_response["edges"][0]
        edge.update(
            relation_type="related_to",
            to_proposed_node_id="may3",
            details="met on that day",
            preferred_relation="met_on",
        )
        client = FakeClient([NODE_RESPONSE, json.dumps(edge_response)])

        output = await self.llm(client).extract_chunk(
            extraction_input=extraction_input()
        )

        self.assertEqual(len(output.repair_items), 1)
        item = output.repair_items[0]
        self.assertEqual(item.item_type, "relation_catalog_gap")
        self.assertEqual(
            {
                key: item.details[key]
                for key in (
                    "chosen_relation",
                    "preferred_relation",
                    "from_type",
                    "to_type",
                    "proposed_edge_id",
                )
            },
            {
                "chosen_relation": "related_to",
                "preferred_relation": "met_on",
                "from_type": "person",
                "to_type": "date",
                "proposed_edge_id": "e1",
            },
        )

    async def test_skips_edge_stage_with_fewer_than_two_nodes(self) -> None:
        single_node = json.loads(NODE_RESPONSE)
        single_node["nodes"] = single_node["nodes"][:1]
        client = FakeClient([json.dumps(single_node)])

        output = await self.llm(client).extract_chunk(
            extraction_input=extraction_input()
        )

        self.assertEqual(len(client.completions.calls), 1)
        self.assertEqual(output.chunk_subgraph.edges, [])


class NodePromptExamplesTests(unittest.TestCase):
    def test_appends_local_examples_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "examples.local.txt"
            path.write_text("Examples\n1. A worked example.\n")
            with patch.object(
                groq_two_stage, "NODE_PROMPT_EXAMPLES_PATH", path
            ):
                prompt = groq_two_stage._node_system_prompt()

        self.assertTrue(prompt.endswith("Examples\n1. A worked example.\n"))

    def test_uses_base_prompt_without_local_examples(self) -> None:
        with patch.object(
            groq_two_stage,
            "NODE_PROMPT_EXAMPLES_PATH",
            Path("/nonexistent/examples.local.txt"),
        ):
            prompt = groq_two_stage._node_system_prompt()

        self.assertEqual(prompt, groq_two_stage._NODE_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
