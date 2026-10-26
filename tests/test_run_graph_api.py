from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import get_run_graph_store
from app.api.main import app
from app.graph.queries.run_graph import RunGraph, RunGraphEdge, RunGraphNode
from app.workspaces.dependencies import require_workspace_member
from tests.helpers import TEST_WORKSPACE_ID, workspace_membership


class FakeRunGraphStore:
    def __init__(self, graph: RunGraph | None) -> None:
        self.graph = graph
        self.calls = []

    async def get_run_graph(self, **kwargs):
        self.calls.append(kwargs)
        return self.graph


def make_graph() -> RunGraph:
    sarah = RunGraphNode(
        node_id=uuid4(),
        node_type="person",
        title="Sarah",
        summary=None,
        importance=0.5,
        confidence=0.5,
        status="active",
        merged_into_node_id=None,
        change_kind="updated",
    )
    matcha = RunGraphNode(
        node_id=uuid4(),
        node_type="concept",
        title="Matcha",
        summary=None,
        importance=0.5,
        confidence=0.5,
        status="active",
        merged_into_node_id=None,
        change_kind="created",
    )
    likes = RunGraphEdge(
        edge_id=uuid4(),
        from_node_id=sarah.node_id,
        to_node_id=matcha.node_id,
        relation_type="likes",
        connector="likes",
        strength=0.3,
        confidence=0.9,
        details=None,
        status="active",
        deleted_reason=None,
        change_kind="created",
    )
    return RunGraph(
        run_id=uuid4(),
        source_id=uuid4(),
        run_status="completed",
        nodes=[matcha, sarah],
        edges=[likes],
    )


class RunGraphApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_workspace_member] = workspace_membership

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_nodes_and_edges_with_change_kinds(self) -> None:
        graph = make_graph()
        store = FakeRunGraphStore(graph)
        app.dependency_overrides[get_run_graph_store] = lambda: store

        response = TestClient(app).get(f"/api/v1/ingestions/{graph.run_id}/graph")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run_id"], str(graph.run_id))
        self.assertEqual(body["run_status"], "completed")
        self.assertEqual(
            [(n["title"], n["change_kind"]) for n in body["nodes"]],
            [("Matcha", "created"), ("Sarah", "updated")],
        )
        self.assertEqual(body["edges"][0]["change_kind"], "created")
        self.assertEqual(
            store.calls,
            [{"workspace_id": TEST_WORKSPACE_ID, "run_id": graph.run_id}],
        )

    def test_unknown_run_returns_404(self) -> None:
        app.dependency_overrides[get_run_graph_store] = lambda: FakeRunGraphStore(None)

        response = TestClient(app).get(f"/api/v1/ingestions/{uuid4()}/graph")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
