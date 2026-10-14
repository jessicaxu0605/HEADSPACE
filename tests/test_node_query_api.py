from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app, get_resolver_store
from app.workflows.resolve_proposed_subgraph import CanonicalNode, NodeCandidate


class FakeResolverStore:
    def __init__(self, candidates: list[NodeCandidate]) -> None:
        self.candidates = candidates
        self.workspace_id = None
        self.proposed_node = None

    async def find_node_candidates(self, *, workspace_id, proposed_node):
        self.workspace_id = workspace_id
        self.proposed_node = proposed_node
        return self.candidates


class NodeQueryApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_ranked_limited_candidates_from_identity_matcher(self) -> None:
        expected_workspace_id = UUID("00000000-0000-0000-0000-000000000002")
        sarah = CanonicalNode(
            uuid4(),
            "person",
            "Sarah Chen",
            identifiers=["Sarah"],
        )
        sara = CanonicalNode(uuid4(), "person", "Sara Cheng")
        ignored = CanonicalNode(uuid4(), "person", "Sarai Chen")
        store = FakeResolverStore(
            [
                NodeCandidate(sarah, "identifier", 0.99, ["exact alias"]),
                NodeCandidate(sara, "fuzzy", 0.95, ["similar title"]),
                NodeCandidate(ignored, "fuzzy", 0.90, ["similar title"]),
            ]
        )
        app.dependency_overrides[get_resolver_store] = lambda: store

        with patch(
            "app.main.ensure_temporary_prototype_workspace",
            return_value=expected_workspace_id,
        ):
            response = TestClient(app).post(
                "/api/v1/nodes/query",
                json={
                    "query": "Sara Chen",
                    "node_type": "person",
                    "aliases": ["Sarah", "Sarah"],
                    "external_ids": [
                        {"provider": " slack ", "external_id": " U123 "}
                    ],
                    "limit": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["query"], "Sara Chen")
        self.assertEqual(len(body["matches"]), 2)
        self.assertEqual(body["matches"][0]["node_id"], str(sarah.node_id))
        self.assertEqual(body["matches"][0]["match_kind"], "identifier")
        self.assertEqual(body["matches"][0]["identifiers"], ["Sarah"])
        self.assertEqual(store.workspace_id, expected_workspace_id)
        self.assertEqual(store.proposed_node.names, ["Sara Chen", "Sarah"])
        self.assertEqual(store.proposed_node.external_ids[0].provider, "slack")
        self.assertEqual(store.proposed_node.external_ids[0].external_id, "U123")

    def test_rejects_invalid_query_inputs(self) -> None:
        app.dependency_overrides[get_resolver_store] = lambda: FakeResolverStore([])
        client = TestClient(app)

        blank = client.post(
            "/api/v1/nodes/query",
            json={"query": "   ", "node_type": "person"},
        )
        invalid_type = client.post(
            "/api/v1/nodes/query",
            json={"query": "Sarah", "node_type": "company"},
        )
        invalid_limit = client.post(
            "/api/v1/nodes/query",
            json={"query": "Sarah", "node_type": "person", "limit": 0},
        )

        self.assertEqual(blank.status_code, 422)
        self.assertEqual(invalid_type.status_code, 422)
        self.assertEqual(invalid_limit.status_code, 422)


if __name__ == "__main__":
    unittest.main()
