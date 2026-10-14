from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.workflows.ingest.workflow import (
    _get_relation_type,
    _persist_review_items,
    IngestionExecutionError,
    StaleTransactionPlanError,
    ingest_content,
)
from app.workflows.ingest.resolve_proposed_subgraph import (
    CanonicalNode,
    ResolutionTransactionPlan,
    ResolveRepairItem,
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.ingest.types import (
    ExtractionContext,
    IngestionRunRecord,
    MemorySourceRecord,
    ProposedGraph,
    ProposedGraphHandoff,
    ProposedNode,
    ProposedSubgraphExtractionResult,
)


class IngestWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_persistence_reads_an_active_database_relation(self) -> None:
        expected = {
            "id": uuid4(),
            "name": "related_to",
            "is_exclusive": False,
            "can_change_over_time": False,
        }

        class Cursor:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class RecordingConnection:
            def __init__(self):
                self.calls = []

            def execute(self, query, params):
                self.calls.append((query, params))
                return Cursor(expected)

        conn = RecordingConnection()
        relation = _get_relation_type(conn, "related_to")

        self.assertEqual(relation, expected)
        self.assertEqual(len(conn.calls), 1)
        self.assertIn("is_active", conn.calls[0][0])

    def test_persistence_rejects_relation_outside_fixed_catalog(self) -> None:
        class EmptyCursor:
            def fetchone(self):
                return None

        class EmptyConnection:
            def execute(self, query, params):
                return EmptyCursor()

        with self.assertRaisesRegex(
            StaleTransactionPlanError,
            "not active in the database catalog",
        ):
            _get_relation_type(
                EmptyConnection(),
                "is_best_friends_with",
            )

    def test_persists_extraction_and_resolution_review_items(self) -> None:
        workspace_id = uuid4()
        source_id = uuid4()
        run_id = uuid4()
        plan = ResolutionTransactionPlan(
            workspace_id=workspace_id,
            source_id=source_id,
            node_bindings=[],
            node_actions=[],
            edge_actions=[],
            repair_items=[
                ResolveRepairItem(
                    item_type="unsupported_relation_type",
                    source_id=source_id,
                    severity="medium",
                    message="No fixed relation fits.",
                    proposed_edge_id="e1",
                    details={"proposed_relation_type": "is_best_friends_with"},
                )
            ],
        )

        class RecordingConnection:
            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, params):
                self.calls.append((query, params))

        conn = RecordingConnection()
        _persist_review_items(
            conn,
            run_id=run_id,
            plan=plan,
            result={
                "repair_items": [
                    {
                        "message": "The relation is unclear.",
                        "item_type": "unclear_relation",
                        "details": {"proposed_edge_id": "e0"},
                    }
                ]
            },
        )

        self.assertEqual(len(conn.calls), 2)
        extraction = conn.calls[0][1]
        resolution = conn.calls[1][1]
        self.assertEqual(extraction["phase"], "extraction")
        self.assertEqual(extraction["item_type"], "unclear_relation")
        self.assertEqual(extraction["proposed_edge_id"], "e0")
        self.assertEqual(resolution["phase"], "resolve")
        self.assertEqual(resolution["item_type"], "unsupported_relation_type")
        self.assertEqual(resolution["proposed_edge_id"], "e1")

        duplicate_conn = RecordingConnection()
        _persist_review_items(
            duplicate_conn,
            run_id=run_id,
            plan=plan,
            result={
                "repair_items": [
                    {
                        "message": "The relation is unclear.",
                        "item_type": "unclear_relation",
                        "details": {"proposed_edge_id": "e1"},
                    }
                ]
            },
        )
        self.assertEqual(len(duplicate_conn.calls), 1)
        self.assertEqual(
            duplicate_conn.calls[0][1]["item_type"],
            "unsupported_relation_type",
        )

    async def test_resolves_extracted_graph_and_persists_transaction_plan(self) -> None:
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
        pending_run = IngestionRunRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            source_id=source.id,
            status="pending",
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        canonical_alice = CanonicalNode(uuid4(), "person", "Alice")
        extraction = ProposedSubgraphExtractionResult(
            proposed_graph=ProposedGraph(
                nodes=[ProposedNode("n1", "person", ["Alice"], "Alice")]
            ),
            proposed_graph_handoff=ProposedGraphHandoff(),
            updated_extraction_context=ExtractionContext("Alice"),
        )

        def update_run(run_id, *, status, result=None, error=None):
            return replace(
                pending_run,
                status=status,
                result=result,
                error=error,
            )

        with patch("app.workflows.ingest.workflow.write_storage_object"), patch(
            "app.workflows.ingest.workflow.create_memory_source",
            return_value=source,
        ), patch(
            "app.workflows.ingest.workflow._get_or_create_ingestion_run",
            return_value=pending_run,
        ), patch(
            "app.workflows.ingest.workflow._set_source_preparation_status"
        ), patch(
            "app.workflows.ingest.workflow._update_ingestion_run",
            side_effect=update_run,
        ), patch(
            "app.workflows.ingest.workflow.prepare_source", return_value=object()
        ), patch(
            "app.workflows.ingest.workflow.extract_proposed_subgraph",
            new=AsyncMock(return_value=extraction),
        ), patch(
            "app.workflows.ingest.workflow.apply_transaction_plan",
            side_effect=lambda *, run_id, plan, result: replace(
                pending_run,
                status="completed",
                result=result,
            ),
        ):
            result = await ingest_content(
                workspace_id=workspace_id,
                source_type="manual_entry",
                content="Alice",
                source_metadata={},
                client_request_id="request-1",
                llm=object(),
                resolver_store=SimpleCanonicalGraphResolverStore(
                    nodes=[canonical_alice]
                ),
            )

        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.result)
        assert result.result is not None
        self.assertEqual(
            result.result["proposed_graph"]["nodes"][0]["proposed_node_id"],
            "n1",
        )
        binding = result.result["transaction_plan"]["node_bindings"][0]
        self.assertEqual(binding["outcome"], "resolved")
        self.assertEqual(binding["node_id"], str(canonical_alice.node_id))

    async def test_apply_failure_is_persisted_for_review_after_rollback(self) -> None:
        workspace_id = uuid4()
        source = MemorySourceRecord(
            workspace_id=workspace_id,
            id=uuid4(),
            source_type="manual_entry",
            storage_object_key="source.txt",
            source_metadata={},
            source_idempotency_key="manual:failed",
        )
        now = datetime.now(timezone.utc)
        pending_run = IngestionRunRecord(
            id=uuid4(),
            workspace_id=workspace_id,
            source_id=source.id,
            status="pending",
            result=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        extraction = ProposedSubgraphExtractionResult(
            proposed_graph=ProposedGraph(),
            proposed_graph_handoff=ProposedGraphHandoff(),
            updated_extraction_context=ExtractionContext("Synthetic"),
        )

        def update_run(run_id, *, status, result=None, error=None):
            return replace(
                pending_run,
                status=status,
                result=result,
                error=error,
            )

        with patch("app.workflows.ingest.workflow.write_storage_object"), patch(
            "app.workflows.ingest.workflow.create_memory_source",
            return_value=source,
        ), patch(
            "app.workflows.ingest.workflow._get_or_create_ingestion_run",
            return_value=pending_run,
        ), patch(
            "app.workflows.ingest.workflow._set_source_preparation_status"
        ), patch(
            "app.workflows.ingest.workflow._update_ingestion_run",
            side_effect=update_run,
        ), patch(
            "app.workflows.ingest.workflow.prepare_source", return_value=object()
        ), patch(
            "app.workflows.ingest.workflow.extract_proposed_subgraph",
            new=AsyncMock(return_value=extraction),
        ), patch(
            "app.workflows.ingest.workflow.apply_transaction_plan",
            side_effect=RuntimeError("edge write failed"),
        ), patch(
            "app.workflows.ingest.workflow._persist_ingestion_failure"
        ) as persist_failure:
            with self.assertRaises(IngestionExecutionError):
                await ingest_content(
                    workspace_id=workspace_id,
                    source_type="manual_entry",
                    content="Alice knows Bob",
                    source_metadata={},
                    client_request_id="failed-request",
                    llm=object(),
                    resolver_store=SimpleCanonicalGraphResolverStore(),
                )

        self.assertEqual(persist_failure.call_args.kwargs["phase"], "apply")
        self.assertEqual(
            persist_failure.call_args.kwargs["error"]["message"],
            "edge write failed",
        )
        self.assertIsNotNone(persist_failure.call_args.kwargs["plan"])


if __name__ == "__main__":
    unittest.main()
