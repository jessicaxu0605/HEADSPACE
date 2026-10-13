from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.workflows.ingest import ingest_content
from app.workflows.resolve_proposed_subgraph import (
    CanonicalNode,
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.types import (
    ExtractionContext,
    IngestionRunRecord,
    MemorySourceRecord,
    ProposedGraph,
    ProposedGraphHandoff,
    ProposedNode,
    ProposedSubgraphExtractionResult,
)


class IngestWorkflowTests(unittest.IsolatedAsyncioTestCase):
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

        with patch("app.workflows.ingest.write_storage_object"), patch(
            "app.workflows.ingest.create_memory_source", return_value=source
        ), patch(
            "app.workflows.ingest._get_or_create_ingestion_run",
            return_value=pending_run,
        ), patch(
            "app.workflows.ingest._set_source_preparation_status"
        ), patch(
            "app.workflows.ingest._update_ingestion_run", side_effect=update_run
        ), patch(
            "app.workflows.ingest.prepare_source", return_value=object()
        ), patch(
            "app.workflows.ingest.extract_proposed_subgraph",
            new=AsyncMock(return_value=extraction),
        ), patch(
            "app.workflows.ingest.apply_transaction_plan",
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


if __name__ == "__main__":
    unittest.main()
