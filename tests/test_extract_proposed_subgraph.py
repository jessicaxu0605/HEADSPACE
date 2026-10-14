from __future__ import annotations

import unittest
from collections.abc import AsyncIterable
from uuid import uuid4

from app.workflows.ingest.extract_proposed_subgraph import (
    InvalidChunkExtractionOutputError,
    ProposedSubgraphLimitError,
    ProposedSubgraphLimits,
    _merge_chunk_subgraph,
    extract_proposed_subgraph,
)
from app.workflows.ingest.resolve_proposed_subgraph import merge_edge_details
from app.workflows.ingest.types import (
    ChunkExtractionInput,
    ChunkExtractionOutput,
    ChunkExtractionRepairContext,
    ExtractionContext,
    ExtractionSourceContext,
    PreparedSource,
    ProposedEdge,
    ProposedEdgeHandoff,
    ProposedEvidence,
    ProposedEvidenceAnnotation,
    ProposedGraph,
    ProposedGraphHandoff,
    ProposedNode,
    ProposedNodeHandoff,
    SourceChunk,
)


async def chunk_stream(*chunks: SourceChunk) -> AsyncIterable[SourceChunk]:
    for chunk in chunks:
        yield chunk


class RecordingLLM:
    def __init__(self, outputs: list[ChunkExtractionOutput]) -> None:
        self.outputs = iter(outputs)
        self.calls: list[ChunkExtractionInput] = []
        self.repair_contexts: list[ChunkExtractionRepairContext | None] = []

    async def extract_chunk(
        self,
        *,
        extraction_input: ChunkExtractionInput,
        repair_context: ChunkExtractionRepairContext | None = None,
    ) -> ChunkExtractionOutput:
        self.calls.append(extraction_input)
        self.repair_contexts.append(repair_context)
        return next(self.outputs)


class ExtractProposedSubgraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_chunk_subgraphs_and_rolls_forward_handoff(self) -> None:
        source_id = uuid4()
        first_chunk = SourceChunk("c1", source_id, "Jessica ")
        second_chunk = SourceChunk("c2", source_id, "met Sarah for lunch.")
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(
                source_id=source_id,
                source_type="manual_entry",
                extraction_metadata={"author": "Jessica"},
            ),
            chunks=chunk_stream(first_chunk, second_chunk),
        )

        jessica = ProposedNode("n1", "person", ["Jessica"], "Jessica")
        first_output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(nodes=[jessica]),
            updated_extraction_context=ExtractionContext("Jessica met someone."),
        )
        sarah = ProposedNode("n2", "person", ["Sarah"], "Sarah")
        met_edge = ProposedEdge(
            proposed_edge_id="e1",
            from_proposed_node_id="n1",
            to_proposed_node_id="n2",
            relation_type="met",
            evidence=[ProposedEvidence("met Sarah", confidence=0.9)],
        )
        final_output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(nodes=[sarah], edges=[met_edge]),
            updated_extraction_context=ExtractionContext("Jessica met Sarah for lunch."),
        )
        llm = RecordingLLM([first_output, final_output])

        result = await extract_proposed_subgraph(prepared, llm, boundary_chars=8)

        self.assertEqual(
            [node.proposed_node_id for node in result.proposed_graph.nodes],
            ["n1", "n2"],
        )
        self.assertEqual(
            [edge.proposed_edge_id for edge in result.proposed_graph.edges],
            ["e1"],
        )
        self.assertEqual(
            result.proposed_graph_handoff,
            ProposedGraphHandoff(
                nodes=[
                    ProposedNodeHandoff("n1", "person", ["Jessica"], "Jessica"),
                    ProposedNodeHandoff("n2", "person", ["Sarah"], "Sarah"),
                ],
                edges=[ProposedEdgeHandoff("e1", "n1", "n2", "met")],
            ),
        )
        self.assertEqual(len(llm.calls), 2)
        first_input = llm.calls[0]
        self.assertEqual(first_input.proposed_graph_handoff.nodes, [])
        self.assertIn("author", first_input.extraction_context.source_summary)
        self.assertEqual(first_input.boundary_context.next_chunk_head, "met Sara")

        second_input = llm.calls[1]
        self.assertIs(second_input.extraction_context, first_output.updated_extraction_context)
        self.assertEqual(
            second_input.proposed_graph_handoff,
            ProposedGraphHandoff(
                nodes=[ProposedNodeHandoff("n1", "person", ["Jessica"], "Jessica")]
            ),
        )
        self.assertEqual(second_input.boundary_context.previous_chunk_tail, "Jessica ")
        self.assertIsNone(second_input.boundary_context.next_chunk_head)

    async def test_repairs_invalid_chunk_output_without_committing_it(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(SourceChunk("c1", source_id, "Alice knows Bob")),
        )
        alice = ProposedNode("n1", "person", ["Alice"], "Alice")
        bob = ProposedNode("n2", "person", ["Bob"], "Bob")
        invalid_edge = ProposedEdge(
            "e1",
            "n1",
            "n2",
            "knows",
            [ProposedEvidence("Alice knows Bob", confidence=1.5)],
        )
        repaired_edge = ProposedEdge(
            "e1",
            "n1",
            "n2",
            "knows",
            [ProposedEvidence("Alice knows Bob", confidence=0.8)],
        )
        invalid_output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(nodes=[alice, bob], edges=[invalid_edge]),
            updated_extraction_context=ExtractionContext("Alice knows Bob"),
        )
        repaired_output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(nodes=[alice, bob], edges=[repaired_edge]),
            updated_extraction_context=ExtractionContext("Alice knows Bob"),
        )
        llm = RecordingLLM([invalid_output, repaired_output])

        result = await extract_proposed_subgraph(prepared, llm)

        self.assertEqual(
            result.proposed_graph.edges[0].evidence[0].raw_content,
            "Alice knows Bob",
        )
        self.assertTrue(result.proposed_graph.edges[0].evidence[0].is_verbatim)
        self.assertEqual(llm.repair_contexts[0], None)
        repair_context = llm.repair_contexts[1]
        self.assertIsNotNone(repair_context)
        self.assertIs(repair_context.invalid_output, invalid_output)
        self.assertEqual(
            [issue.code for issue in repair_context.issues],
            ["invalid_evidence_confidence"],
        )

    async def test_bundles_issues_and_fails_after_repair_budget(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(SourceChunk("c1", source_id, "Jessica")),
        )
        invalid_output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(
                nodes=[ProposedNode("n1", "person", [], "")]
            ),
            updated_extraction_context=ExtractionContext("Jessica"),
        )
        llm = RecordingLLM([invalid_output, invalid_output, invalid_output])

        with self.assertRaises(InvalidChunkExtractionOutputError) as raised:
            await extract_proposed_subgraph(prepared, llm)

        self.assertEqual(raised.exception.attempts, 3)
        self.assertIs(raised.exception.invalid_output, invalid_output)
        self.assertEqual(
            {issue.code for issue in raised.exception.issues},
            {
                "empty_primary_name",
                "missing_node_names",
                "primary_name_missing_from_names",
            },
        )
        self.assertEqual(len(llm.repair_contexts), 3)
        self.assertIsNone(llm.repair_contexts[0])
        self.assertIsNotNone(llm.repair_contexts[1])
        self.assertIsNotNone(llm.repair_contexts[2])

    async def test_repeated_node_id_adds_names_and_refines_metadata(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(
                SourceChunk("c1", source_id, "Jessica"),
                SourceChunk("c2", source_id, "Jess"),
            ),
        )
        outputs = [
            ChunkExtractionOutput(
                chunk_subgraph=ProposedGraph(
                    nodes=[
                        ProposedNode(
                            "n1",
                            "person",
                            ["Jessica"],
                            "Jessica",
                            summary="A person",
                            type_specific={"role": "engineer", "city": "Toronto"},
                        )
                    ]
                ),
                updated_extraction_context=ExtractionContext("Jessica"),
            ),
            ChunkExtractionOutput(
                chunk_subgraph=ProposedGraph(
                    nodes=[
                        ProposedNode(
                            "n1",
                            "person",
                            ["Jess"],
                            "Jess",
                            summary="Jessica, who goes by Jess",
                            type_specific={"city": "Singapore"},
                        )
                    ]
                ),
                updated_extraction_context=ExtractionContext("Jessica is also called Jess"),
            ),
        ]

        result = await extract_proposed_subgraph(prepared, RecordingLLM(outputs))

        merged_node = result.proposed_graph.nodes[0]
        self.assertEqual(merged_node.names, ["Jessica", "Jess"])
        self.assertEqual(merged_node.primary_reference_name, "Jess")
        self.assertEqual(merged_node.summary, "Jessica, who goes by Jess")
        self.assertEqual(
            merged_node.type_specific,
            {"role": "engineer", "city": "Singapore"},
        )

    async def test_repeated_node_id_cannot_change_type(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(
                SourceChunk("c1", source_id, "Jordan"),
                SourceChunk("c2", source_id, "Jordan"),
            ),
        )
        outputs = [
            ChunkExtractionOutput(
                chunk_subgraph=ProposedGraph(
                    nodes=[ProposedNode("n1", "person", ["Jordan"], "Jordan")]
                ),
                updated_extraction_context=ExtractionContext("Jordan is a person"),
            ),
            ChunkExtractionOutput(
                chunk_subgraph=ProposedGraph(
                    nodes=[ProposedNode("n1", "location", ["Jordan"], "Jordan")]
                ),
                updated_extraction_context=ExtractionContext("Jordan is also a country"),
            ),
        ]

        with self.assertRaisesRegex(
            InvalidChunkExtractionOutputError,
            "requires a new proposed node ID",
        ):
            await extract_proposed_subgraph(
                prepared,
                RecordingLLM(outputs),
                max_repair_attempts=0,
            )

    async def test_accepts_paraphrased_evidence(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(SourceChunk("c1", source_id, "Alice knows Bob")),
        )
        alice = ProposedNode("n1", "person", ["Alice"], "Alice")
        bob = ProposedNode("n2", "person", ["Bob"], "Bob")
        edge = ProposedEdge(
            "e1",
            "n1",
            "n2",
            "knows",
            evidence=[ProposedEvidence("Alice works with Bob", confidence=0.8)],
        )
        output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(nodes=[alice, bob], edges=[edge]),
            updated_extraction_context=ExtractionContext("Alice knows Bob"),
        )

        result = await extract_proposed_subgraph(
            prepared,
            RecordingLLM([output]),
            max_repair_attempts=0,
        )

        evidence = result.proposed_graph.edges[0].evidence[0]
        self.assertEqual(evidence.raw_content, "Alice works with Bob")
        self.assertFalse(evidence.is_verbatim)

    async def test_rejects_evidence_scores_outside_unit_interval(self) -> None:
        for confidence, strength_boost, expected_message in (
            (1.1, None, "confidence"),
            (0.8, -0.1, "strength boost"),
        ):
            with self.subTest(expected_message=expected_message):
                source_id = uuid4()
                prepared = PreparedSource(
                    source_context=ExtractionSourceContext(
                        source_id,
                        "manual_entry",
                        {},
                    ),
                    chunks=chunk_stream(
                        SourceChunk("c1", source_id, "Alice knows Bob")
                    ),
                )
                evidence = ProposedEvidence(
                    "Alice knows Bob",
                    confidence=confidence,
                    strength_boost=strength_boost,
                )
                output = ChunkExtractionOutput(
                    chunk_subgraph=ProposedGraph(
                        nodes=[
                            ProposedNode("n1", "person", ["Alice"], "Alice"),
                            ProposedNode("n2", "person", ["Bob"], "Bob"),
                        ],
                        edges=[
                            ProposedEdge("e1", "n1", "n2", "knows", [evidence])
                        ],
                    ),
                    updated_extraction_context=ExtractionContext("Alice knows Bob"),
                )

                with self.assertRaisesRegex(
                    InvalidChunkExtractionOutputError,
                    expected_message,
                ):
                    await extract_proposed_subgraph(
                        prepared,
                        RecordingLLM([output]),
                        max_repair_attempts=0,
                    )

    async def test_rejects_out_of_bounds_evidence_annotation(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(SourceChunk("c1", source_id, "She knows Bob")),
        )
        alice = ProposedNode("n1", "person", ["Alice"], "Alice")
        bob = ProposedNode("n2", "person", ["Bob"], "Bob")
        evidence = ProposedEvidence(
            "She knows Bob",
            confidence=0.9,
            annotations=[ProposedEvidenceAnnotation(10, 10, "n2")],
        )
        output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(
                nodes=[alice, bob],
                edges=[ProposedEdge("e1", "n1", "n2", "knows", [evidence])],
            ),
            updated_extraction_context=ExtractionContext("Alice knows Bob"),
        )

        with self.assertRaisesRegex(
            InvalidChunkExtractionOutputError,
            "falls outside",
        ):
            await extract_proposed_subgraph(
                prepared,
                RecordingLLM([output]),
                max_repair_attempts=0,
            )

    async def test_rejects_invalid_node_record_before_merge(self) -> None:
        source_id = uuid4()
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(SourceChunk("c1", source_id, "Jessica")),
        )
        output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(
                nodes=[ProposedNode("n1", "person", ["Jessica"], "Jess")]
            ),
            updated_extraction_context=ExtractionContext("Jessica"),
        )

        with self.assertRaisesRegex(
            InvalidChunkExtractionOutputError,
            "must appear in its names",
        ):
            await extract_proposed_subgraph(
                prepared,
                RecordingLLM([output]),
                max_repair_attempts=0,
            )

    async def test_halts_job_when_accumulated_graph_reaches_limit(self) -> None:
        source_id = uuid4()
        chunk = SourceChunk("c1", source_id, "Jessica")
        prepared = PreparedSource(
            source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
            chunks=chunk_stream(chunk),
        )
        node = ProposedNode("n1", "person", ["Jessica"], "Jessica")
        output = ChunkExtractionOutput(
            chunk_subgraph=ProposedGraph(nodes=[node]),
            updated_extraction_context=ExtractionContext("Jessica"),
        )

        with self.assertRaises(ProposedSubgraphLimitError):
            await extract_proposed_subgraph(
                prepared,
                RecordingLLM([output]),
                limits=ProposedSubgraphLimits(max_nodes=1, max_edges=1),
            )


class EdgeDetailsTests(unittest.TestCase):
    def test_merge_edge_details_accumulates_without_repeats(self) -> None:
        self.assertIsNone(merge_edge_details(None, None))
        self.assertIsNone(merge_edge_details(None, "   "))
        self.assertEqual(merge_edge_details(None, " usually  iced "), "usually iced")
        self.assertEqual(merge_edge_details("usually iced", None), "usually iced")
        self.assertEqual(
            merge_edge_details(
                "especially strawberry matcha lattes", "usually orders it iced"
            ),
            "especially strawberry matcha lattes; usually orders it iced",
        )
        self.assertEqual(
            merge_edge_details("strawberry matcha lattes; iced", "Iced"),
            "strawberry matcha lattes; iced",
        )
        self.assertEqual(
            merge_edge_details("lattes", "especially strawberry lattes"),
            "especially strawberry lattes",
        )

    def test_chunk_merge_accumulates_details_without_changing_identity(self) -> None:
        sarah = ProposedNode("n1", "person", ["Sarah"], "Sarah")
        matcha = ProposedNode("n2", "concept", ["Matcha"], "Matcha")
        first = ProposedGraph(
            nodes=[sarah, matcha],
            edges=[
                ProposedEdge(
                    "e1",
                    "n1",
                    "n2",
                    "likes",
                    [ProposedEvidence("loves strawberry matcha lattes", 0.9)],
                    details="especially strawberry matcha lattes",
                )
            ],
        )
        second = ProposedGraph(
            edges=[
                ProposedEdge(
                    "e1",
                    "n1",
                    "n2",
                    "likes",
                    [ProposedEvidence("ordered it iced again", 0.7)],
                    details="usually orders it iced",
                )
            ],
        )

        merged = _merge_chunk_subgraph(_merge_chunk_subgraph(ProposedGraph(), first), second)

        self.assertEqual(len(merged.edges), 1)
        self.assertEqual(merged.edges[0].relation_type, "likes")
        self.assertEqual(
            merged.edges[0].details,
            "especially strawberry matcha lattes; usually orders it iced",
        )
        self.assertEqual(len(merged.edges[0].evidence), 2)
        self.assertEqual(first.edges[0].details, "especially strawberry matcha lattes")


if __name__ == "__main__":
    unittest.main()
