from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import get_node_search_store
from app.api.main import app
from app.search.node_search import NodeSearchResult
from app.workspaces.dependencies import require_workspace_member
from tests.helpers import TEST_WORKSPACE_ID, workspace_membership


def _result(node_type, title, match_kind, score, reasons, identifiers=()):
    return NodeSearchResult(
        uuid4(), node_type, title, match_kind, score, list(identifiers), reasons
    )


class FakeSearchStore:
    def __init__(self, results: list[NodeSearchResult]) -> None:
        self.results = results
        self.calls = []

    async def search_nodes(self, **kwargs):
        self.calls.append(kwargs)
        return self.results[: kwargs["limit"]]


class NodeQueryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_workspace_member] = workspace_membership

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_ranked_limited_results_from_search_store(self) -> None:
        expected_workspace_id = TEST_WORKSPACE_ID
        sarah = _result(
            "person", "Sarah Chen", "identifier", 0.99, ["exact alias"], ["Sarah"]
        )
        store = FakeSearchStore(
            [
                sarah,
                _result("person", "Sara Cheng", "fuzzy", 0.95, ["similar title"]),
                _result("person", "Sarai Chen", "profile", 0.70, ["related"]),
            ]
        )
        app.dependency_overrides[get_node_search_store] = lambda: store

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
        self.assertEqual(body["matches"][1]["match_score"], 0.95)
        self.assertEqual(body["matches"][0]["match_kind"], "identifier")
        self.assertEqual(body["matches"][0]["identifiers"], ["Sarah"])
        call = store.calls[0]
        self.assertEqual(call["workspace_id"], expected_workspace_id)
        self.assertEqual(call["query"], "Sara Chen")
        self.assertEqual(call["node_type"], "person")
        self.assertEqual(call["aliases"], ["Sarah"])
        self.assertEqual(call["external_ids"][0].provider, "slack")
        self.assertEqual(call["external_ids"][0].external_id, "U123")
        self.assertEqual(call["limit"], 2)

    def test_searches_without_a_type_filter_when_type_is_omitted(self) -> None:
        store = FakeSearchStore(
            [_result("person", "Jordan", "normalized_title", 0.98, ["exact title"])]
        )
        app.dependency_overrides[get_node_search_store] = lambda: store

        response = TestClient(app).post(
            "/api/v1/nodes/query",
            json={"query": "Jordan", "limit": 5},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["node_type"])
        self.assertEqual(len(response.json()["matches"]), 1)
        self.assertEqual(len(store.calls), 1)
        self.assertIsNone(store.calls[0]["node_type"])

    def test_rejects_invalid_query_inputs(self) -> None:
        app.dependency_overrides[get_node_search_store] = lambda: FakeSearchStore([])
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
