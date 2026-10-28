from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import dependencies
from app.api.dependencies import (
    get_enrichment_llm,
    get_extraction_llm,
    get_resolver_store,
)
from app.api.main import app
from app.workflows.ingest.models import IngestionRunRecord
from app.api.workspace_access import require_workspace_member
from tests.helpers import TEST_WORKSPACE_ID, workspace_membership


class ExtractionDependencyTests(unittest.TestCase):
    @patch("app.api.dependencies.GroqTwoStageExtractionLLM")
    @patch("app.api.dependencies.load_active_relation_types")
    def test_uses_two_stage_extraction(
        self, load_relation_types, two_stage_extractor
    ) -> None:
        relation_definitions = [object()]
        expected = object()
        load_relation_types.return_value = relation_definitions
        two_stage_extractor.return_value = expected

        result = dependencies.get_extraction_llm()

        self.assertIs(result, expected)
        two_stage_extractor.assert_called_once_with(
            relation_definitions=relation_definitions
        )


class FakeLLM:
    pass


class FakeResolverStore:
    pass


def run_record(*, status: str = "completed") -> IngestionRunRecord:
    now = datetime.now(timezone.utc)
    return IngestionRunRecord(
        id=uuid4(),
        workspace_id=TEST_WORKSPACE_ID,
        source_id=uuid4(),
        status=status,
        result={
            "proposed_graph": {"nodes": [], "edges": []},
            "transaction_plan": {
                "node_bindings": [],
                "node_actions": [],
                "edge_actions": [],
                "repair_items": [],
            },
        }
        if status == "completed"
        else None,
        error=None,
        created_at=now,
        updated_at=now,
    )


class IngestionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_workspace_member] = workspace_membership
        app.dependency_overrides[get_extraction_llm] = lambda: FakeLLM()
        app.dependency_overrides[get_enrichment_llm] = lambda: FakeLLM()
        self.resolver_store = FakeResolverStore()
        app.dependency_overrides[get_resolver_store] = lambda: self.resolver_store
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_creates_ingestion_and_returns_persisted_result(self) -> None:
        expected = run_record()
        with patch(
            "app.api.routes.ingestions.ingest_content",
            new=AsyncMock(return_value=expected),
        ) as ingest, patch("builtins.print") as print_output:
            response = self.client.post(
                "/api/v1/ingestions",
                json={
                    "content": "Alice knows Bob.",
                    "client_request_id": "browser-request-1",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["id"], str(expected.id))
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(
            response.json()["result"]["proposed_graph"],
            {"nodes": [], "edges": []},
        )
        self.assertEqual(ingest.await_args.kwargs["source_type"], "manual_entry")
        self.assertIs(
            ingest.await_args.kwargs["resolver_store"],
            self.resolver_store,
        )
        self.assertEqual(
            ingest.await_args.kwargs["workspace_id"],
            expected.workspace_id,
        )
        printed = print_output.call_args.args[0]
        self.assertIn(f"Proposed subgraph for ingestion run {expected.id}", printed)
        self.assertIn('"nodes": []', printed)
        self.assertIn(
            f"Resolution transaction plan for ingestion run {expected.id}",
            printed,
        )
        self.assertIn('"node_bindings": []', printed)

    def test_rejects_blank_or_oversized_content(self) -> None:
        blank = self.client.post(
            "/api/v1/ingestions",
            json={"content": "   ", "client_request_id": "blank"},
        )
        oversized = self.client.post(
            "/api/v1/ingestions",
            json={
                "content": "x" * (2 * 1024 * 1024 + 1),
                "client_request_id": "large",
            },
        )

        self.assertEqual(blank.status_code, 422)
        self.assertEqual(oversized.status_code, 422)

    def test_get_returns_404_for_unknown_run(self) -> None:
        run_id = uuid4()
        with patch(
            "app.api.routes.ingestions.get_ingestion_run", return_value=None
        ) as get_run:
            response = self.client.get(f"/api/v1/ingestions/{run_id}")

        self.assertEqual(response.status_code, 404)
        get_run.assert_called_once_with(
            workspace_id=TEST_WORKSPACE_ID,
            run_id=run_id,
        )


if __name__ == "__main__":
    unittest.main()
