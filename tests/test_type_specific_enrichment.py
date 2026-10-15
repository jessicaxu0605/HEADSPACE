from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.workflows.ingest.enrich_type_specific import enrich_type_specific
from app.workflows.ingest.groq_type_specific_enrichment import (
    TYPE_SPECIFIC_ENRICHMENT_OUTPUT_SCHEMA,
    parse_type_specific_enrichment_output,
)
from app.workflows.ingest.resolve_proposed_subgraph import (
    CanonicalNode,
    CreateCanonicalNode,
    CreateNodeExternalId,
    FillCanonicalNodeSummary,
    FillCanonicalNodeTypeSpecific,
    SimpleCanonicalGraphResolverStore,
    resolve_proposed_subgraph,
)
from app.workflows.ingest.type_specific import (
    displayable_type_specific,
    normalize_type_specific,
    type_specific_identity_key,
    with_type_specific_identity,
)
from app.workflows.ingest.types import (
    ExtractionContext,
    ExtractionSourceContext,
    IngestionRunRecord,
    MemorySourceRecord,
    PreparedSource,
    ProposedGraph,
    ProposedExternalId,
    ProposedGraphHandoff,
    ProposedNode,
    ProposedSubgraphExtractionResult,
    SourceChunk,
    TypeSpecificEnrichmentInput,
    TypeSpecificPatch,
)
from app.workflows.ingest.workflow import ingest_content


def _prepared_source(*texts: str) -> PreparedSource:
    source_id = uuid4()

    async def chunks():
        for index, text in enumerate(texts):
            yield SourceChunk(f"c{index}", source_id, text)

    return PreparedSource(
        source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
        chunks=chunks(),
    )


class RecordingEnrichmentLLM:
    def __init__(self, patches: list[list[TypeSpecificPatch]]) -> None:
        self.patches = list(patches)
        self.inputs: list[TypeSpecificEnrichmentInput] = []

    async def enrich_chunk(
        self,
        *,
        enrichment_input: TypeSpecificEnrichmentInput,
    ) -> list[TypeSpecificPatch]:
        self.inputs.append(enrichment_input)
        return self.patches.pop(0)


class TypeSpecificFieldTests(unittest.TestCase):
    def test_normalizes_known_fields_and_drops_the_rest(self) -> None:
        self.assertEqual(
            normalize_type_specific(
                "person",
                {
                    "pronouns": " she/her ",
                    "display_name": "Sarah",
                    "favourite_colour": "green",
                    "relationship_context": "",
                },
            ),
            {"pronouns": "she/her"},
        )
        self.assertIsNone(
            normalize_type_specific("task", {"task_status": "someday"})
        )
        # Types without a field catalog are left alone.
        self.assertEqual(
            normalize_type_specific("concept", {"anything": 1}), {"anything": 1}
        )

    def test_identity_fields_become_external_ids_not_type_specific(self) -> None:
        node = with_type_specific_identity(
            ProposedNode(
                "s",
                "person",
                ["Sarah"],
                "Sarah",
                type_specific={
                    "email": " Sarah@Example.com ",
                    "instagram": "@SarahC",
                    "phone_number": "+1 (519) 555-0100",
                },
                external_ids=[ProposedExternalId("email", "sarah@example.com")],
            )
        )

        self.assertIsNone(node.type_specific)
        self.assertEqual(
            node.external_ids,
            [
                ProposedExternalId("email", "sarah@example.com"),
                ProposedExternalId("phone", "+15195550100"),
                ProposedExternalId("instagram", "sarahc"),
            ],
        )

    def test_only_display_fields_are_displayable(self) -> None:
        self.assertEqual(
            [
                (spec.label, value)
                for spec, value in displayable_type_specific(
                    "task", {"task_status": "in_progress", "due_at": "2026-10-17"}
                )
            ],
            [("due", "2026-10-17T00:00:00"), ("status", "in_progress")],
        )
        # A date node's date restates its title.
        self.assertEqual(
            displayable_type_specific(
                "date", {"date_value": "1935-06-01", "precision": "month"}
            ),
            [],
        )

    def test_date_identity_key_respects_precision(self) -> None:
        self.assertEqual(
            type_specific_identity_key(
                "date", {"date_value": "2026-03-01", "precision": "month"}
            ),
            "month:2026-03",
        )
        self.assertEqual(
            type_specific_identity_key("date", {"date_value": "2026-03-03"}),
            "day:2026-03-03",
        )
        self.assertIsNone(type_specific_identity_key("person", {"email": "a@b.co"}))


class EnrichTypeSpecificTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_mentioned_nodes_and_merges_normalized_values(self) -> None:
        sarah = ProposedNode(
            "sarah", "person", ["Sarah"], "Sarah Chen", type_specific={"bogus": 1}
        )
        task = ProposedNode("deck", "task", ["pitch deck"], "Finish pitch deck")
        concept = ProposedNode("matcha", "concept", ["matcha"], "Matcha")
        graph = ProposedGraph(nodes=[sarah, task, concept])
        llm = RecordingEnrichmentLLM(
            [
                [TypeSpecificPatch("sarah", {"email": "SARAH@example.com"})],
                [
                    TypeSpecificPatch(
                        "deck", {"due_at": "2026-10-17", "task_status": "In Progress"}
                    ),
                    TypeSpecificPatch("unknown", {"email": "x@example.com"}),
                ],
            ]
        )

        await enrich_type_specific(
            _prepared_source(
                "Sarah's email is sarah@example.com. She loves matcha.",
                "Nothing relevant here.",
                "The pitch deck is due Friday and I'm working on it.",
            ),
            graph,
            llm,
            extraction_context=ExtractionContext("notes", temporal_anchor="Oct 15"),
            reference_date=date(2026, 10, 15),
        )

        self.assertEqual(len(llm.inputs), 2)
        self.assertEqual(
            [node.proposed_node_id for node in llm.inputs[0].nodes],
            ["sarah", "matcha"],
        )
        self.assertIsNone(llm.inputs[0].nodes[0].type_specific)
        self.assertEqual(llm.inputs[0].reference_date, "2026-10-15")
        self.assertEqual(llm.inputs[0].temporal_anchor, "Oct 15")
        # The email is an identity field, so it is not stored in type_specific;
        # resolution drops the unknown key extraction left there.
        self.assertIsNone(normalize_type_specific("person", sarah.type_specific))
        self.assertEqual(
            sarah.external_ids, [ProposedExternalId("email", "sarah@example.com")]
        )
        self.assertEqual(
            task.type_specific,
            {"due_at": "2026-10-17T00:00:00", "task_status": "in_progress"},
        )
        self.assertIsNone(concept.type_specific)


    async def test_summaries_apply_to_any_node_type(self) -> None:
        singapore = ProposedNode("sg", "location", ["Singapore"], "Singapore")
        matcha = ProposedNode(
            "matcha", "concept", ["matcha"], "Matcha", summary="Green tea."
        )
        graph = ProposedGraph(nodes=[singapore, matcha])
        llm = RecordingEnrichmentLLM(
            [
                [
                    TypeSpecificPatch(
                        "sg", {}, "Singapore is a city-state in Southeast Asia."
                    ),
                    TypeSpecificPatch("matcha", {}, "  "),
                ]
            ]
        )

        await enrich_type_specific(
            _prepared_source("Flying to Singapore for matcha."), graph, llm
        )

        self.assertEqual(
            [node.proposed_node_id for node in llm.inputs[0].nodes], ["sg", "matcha"]
        )
        self.assertEqual(
            singapore.summary, "Singapore is a city-state in Southeast Asia."
        )
        self.assertEqual(matcha.summary, "Green tea.")


class GroqEnrichmentParsingTests(unittest.TestCase):
    def test_keeps_only_fields_of_each_nodes_type(self) -> None:
        item = {
            name: None
            for name in TYPE_SPECIFIC_ENRICHMENT_OUTPUT_SCHEMA["properties"]["nodes"][
                "items"
            ]["properties"]
        }
        raw = json.dumps(
            {
                "nodes": [
                    {**item, "proposed_node_id": "sarah", "pronouns": "she/her",
                     "due_at": "2026-10-17"},
                    {**item, "proposed_node_id": "ghost", "pronouns": "he/him"},
                    {**item, "proposed_node_id": "deck"},
                ]
            }
        )

        patches = parse_type_specific_enrichment_output(
            raw, {"sarah": "person", "deck": "task"}
        )

        self.assertEqual(patches, [TypeSpecificPatch("sarah", {"pronouns": "she/her"})])

    def test_keeps_summaries_for_types_without_fields(self) -> None:
        item = {
            name: None
            for name in TYPE_SPECIFIC_ENRICHMENT_OUTPUT_SCHEMA["properties"]["nodes"][
                "items"
            ]["properties"]
        }
        raw = json.dumps(
            {
                "nodes": [
                    {
                        **item,
                        "proposed_node_id": "sg",
                        "summary": " A city-state. ",
                        "address": "1 Main St",
                    }
                ]
            }
        )

        self.assertEqual(
            parse_type_specific_enrichment_output(raw, {"sg": "concept"}),
            [TypeSpecificPatch("sg", {}, "A city-state.")],
        )

    def test_schema_is_strict_and_excludes_lookup_fields(self) -> None:
        item = TYPE_SPECIFIC_ENRICHMENT_OUTPUT_SCHEMA["properties"]["nodes"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(set(item["required"]), set(item["properties"]))
        self.assertNotIn("google_place_id", item["properties"])
        self.assertIn(None, item["properties"]["precision"]["enum"])


class TypeSpecificResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def _resolve(self, nodes: list[ProposedNode], canonical: list[CanonicalNode]):
        return await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=ProposedGraph(nodes=nodes),
            store=SimpleCanonicalGraphResolverStore(nodes=canonical),
        )

    async def test_matching_date_value_resolves_differently_named_dates(self) -> None:
        existing = CanonicalNode(
            uuid4(), "date", "March 3, 2026", type_specific={"date_value": "2026-03-03"}
        )
        plan = await self._resolve(
            [
                ProposedNode(
                    "d", "date", ["3/3"], "3/3",
                    type_specific={"date_value": "2026-03-03", "precision": "day"},
                )
            ],
            [existing],
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, existing.node_id)
        self.assertEqual(
            [a for a in plan.node_actions if isinstance(a, FillCanonicalNodeTypeSpecific)],
            [FillCanonicalNodeTypeSpecific(existing.node_id, {"precision": "day"})],
        )

    async def test_summary_fills_only_a_resolved_node_without_one(self) -> None:
        bare = CanonicalNode(uuid4(), "location", "Singapore")
        described = CanonicalNode(
            uuid4(), "location", "Waterloo", summary="A city in Ontario."
        )
        plan = await self._resolve(
            [
                ProposedNode(
                    "sg", "location", ["Singapore"], "Singapore", summary="City-state."
                ),
                ProposedNode(
                    "wat", "location", ["Waterloo"], "Waterloo", summary="Tech hub."
                ),
            ],
            [bare, described],
        )

        self.assertEqual(
            [a for a in plan.node_actions if isinstance(a, FillCanonicalNodeSummary)],
            [FillCanonicalNodeSummary(bare.node_id, "City-state.")],
        )

    async def test_different_date_value_blocks_a_name_match(self) -> None:
        existing = CanonicalNode(
            uuid4(), "date", "Friday", type_specific={"date_value": "2026-10-09"}
        )
        plan = await self._resolve(
            [
                ProposedNode(
                    "d", "date", ["Friday"], "Friday",
                    type_specific={"date_value": "2026-10-16"},
                )
            ],
            [existing],
        )

        self.assertEqual(plan.node_bindings[0].outcome, "created")
        create = plan.node_actions[0]
        assert isinstance(create, CreateCanonicalNode)
        self.assertEqual(create.type_specific, {"date_value": "2026-10-16"})

    async def test_resolved_node_keeps_existing_values_and_reports_conflicts(self) -> None:
        existing = CanonicalNode(
            uuid4(),
            "person",
            "Sarah Chen",
            type_specific={"pronouns": "she/her"},
        )
        plan = await self._resolve(
            [
                ProposedNode(
                    "s", "person", ["Sarah Chen"], "Sarah Chen",
                    type_specific={
                        "pronouns": "they/them",
                        "relationship_context": "college roommate",
                        "instagram": "@sarahc",
                    },
                )
            ],
            [existing],
        )

        self.assertIn(
            FillCanonicalNodeTypeSpecific(
                existing.node_id, {"relationship_context": "college roommate"}
            ),
            plan.node_actions,
        )
        self.assertIn(
            CreateNodeExternalId(existing.node_id, "instagram", "sarahc"),
            plan.node_actions,
        )
        [conflict] = plan.repair_items
        self.assertEqual(conflict.item_type, "conflicting_type_specific")
        self.assertEqual(
            conflict.details["fields"],
            {"pronouns": {"existing": "she/her", "incoming": "they/them"}},
        )

    async def test_type_specific_email_resolves_through_external_id(self) -> None:
        canonical = CanonicalNode(
            uuid4(),
            "person",
            "S. Chen",
            external_ids=[ProposedExternalId("email", "sarah@example.com")],
        )
        plan = await self._resolve(
            [
                ProposedNode(
                    "s", "person", ["Sar"], "Sar",
                    type_specific={"email": "Sarah@Example.com"},
                )
            ],
            [canonical],
        )

        self.assertEqual(plan.node_bindings[0].node_id, canonical.node_id)
        self.assertEqual(plan.node_bindings[0].match_score, 1.0)


class IngestEnrichmentFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_enrichment_failure_is_recorded_and_ingest_continues(self) -> None:
        workspace_id = uuid4()
        source = MemorySourceRecord(
            workspace_id=workspace_id,
            id=uuid4(),
            source_type="manual_entry",
            storage_object_key="source.txt",
            source_metadata={},
            source_idempotency_key="manual:1",
        )
        now = datetime.now(timezone.utc)
        run = IngestionRunRecord(
            uuid4(), workspace_id, source.id, "pending", None, None, now, now
        )
        extraction = ProposedSubgraphExtractionResult(
            proposed_graph=ProposedGraph(
                nodes=[ProposedNode("n1", "person", ["Alice"], "Alice")]
            ),
            proposed_graph_handoff=ProposedGraphHandoff(),
            updated_extraction_context=ExtractionContext("Alice"),
        )

        class FailingEnrichmentLLM:
            async def enrich_chunk(self, *, enrichment_input):
                raise RuntimeError("rate limited")

        with patch("app.workflows.ingest.workflow.write_storage_object"), patch(
            "app.workflows.ingest.workflow.create_memory_source", return_value=source
        ), patch(
            "app.workflows.ingest.workflow._get_or_create_ingestion_run",
            return_value=run,
        ), patch(
            "app.workflows.ingest.workflow._set_source_preparation_status"
        ), patch(
            "app.workflows.ingest.workflow._update_ingestion_run",
            side_effect=lambda run_id, *, status, result=None, error=None: replace(
                run, status=status
            ),
        ), patch(
            "app.workflows.ingest.workflow.prepare_source",
            side_effect=lambda _: _prepared_source("Alice says hi."),
        ), patch(
            "app.workflows.ingest.workflow.extract_proposed_subgraph",
            new=AsyncMock(return_value=extraction),
        ), patch(
            "app.workflows.ingest.workflow.apply_transaction_plan",
            side_effect=lambda *, run_id, plan, result: replace(
                run, status="completed", result=result
            ),
        ), self.assertLogs("app.workflows.ingest.workflow", "WARNING"):
            result = await ingest_content(
                workspace_id=workspace_id,
                source_type="manual_entry",
                content="Alice says hi.",
                source_metadata={},
                client_request_id="request-1",
                llm=object(),
                enrichment_llm=FailingEnrichmentLLM(),
                resolver_store=SimpleCanonicalGraphResolverStore(),
            )

        self.assertEqual(result.status, "completed")
        assert result.result is not None
        [item] = result.result["repair_items"]
        self.assertEqual(item["item_type"], "type_specific_enrichment_failed")
        self.assertEqual(
            result.result["transaction_plan"]["node_bindings"][0]["outcome"],
            "created",
        )


if __name__ == "__main__":
    unittest.main()
