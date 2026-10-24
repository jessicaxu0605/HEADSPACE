from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app, get_neighborhood_store
from app.graph.queries.node_neighborhood import (
    NeighborhoodEdge,
    NeighborhoodNode,
    SimpleNodeNeighborhoodStore,
)
from app.workspaces.dependencies import require_workspace_member
from tests.helpers import workspace_membership


class NodeNeighborhoodWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_traverses_cycles_once_and_keeps_unique_induced_edges(self) -> None:
        a, b, c, d, e = [uuid4() for _ in range(5)]
        edge_ab = NeighborhoodEdge(uuid4(), a, b, "knows", 0.8, 0.9)
        edge_bc = NeighborhoodEdge(uuid4(), b, c, "knows", 0.7, 0.8)
        edge_ca = NeighborhoodEdge(uuid4(), c, a, "knows", 0.6, 0.7)
        edge_ab_parallel = NeighborhoodEdge(
            uuid4(), a, b, "works_with", 0.5, 0.6
        )
        edge_bd = NeighborhoodEdge(uuid4(), b, d, "knows", 0.4, 0.5)
        edge_de = NeighborhoodEdge(uuid4(), d, e, "knows", 0.3, 0.4)
        store = SimpleNodeNeighborhoodStore(
            nodes=[
                NeighborhoodNode(a, "person", "A"),
                NeighborhoodNode(b, "person", "B"),
                NeighborhoodNode(c, "person", "C"),
                NeighborhoodNode(d, "person", "D"),
                NeighborhoodNode(e, "person", "E"),
            ],
            edges=[
                edge_ab,
                edge_bc,
                edge_ca,
                edge_ab_parallel,
                edge_bd,
                edge_de,
                edge_ab,  # A duplicate snapshot row must not duplicate output.
            ],
        )

        graph = await store.get_node_neighborhood(
            workspace_id=uuid4(),
            start_node_id=a,
            depth=1,
        )

        self.assertIsNotNone(graph)
        assert graph is not None
        self.assertEqual(
            [(node.title, node.distance) for node in graph.nodes],
            [("A", 0), ("B", 1), ("C", 1)],
        )
        self.assertEqual(
            {edge.edge_id for edge in graph.edges},
            {
                edge_ab.edge_id,
                edge_bc.edge_id,
                edge_ca.edge_id,
                edge_ab_parallel.edge_id,
            },
        )
        self.assertEqual(len(graph.edges), 4)

    async def test_respects_depth_and_returns_none_for_missing_start(self) -> None:
        a, b, c = uuid4(), uuid4(), uuid4()
        store = SimpleNodeNeighborhoodStore(
            nodes=[
                NeighborhoodNode(a, "person", "A"),
                NeighborhoodNode(b, "person", "B"),
                NeighborhoodNode(c, "person", "C"),
            ],
            edges=[
                NeighborhoodEdge(uuid4(), a, b, "knows", 0.8, 0.9),
                NeighborhoodEdge(uuid4(), b, c, "knows", 0.7, 0.8),
            ],
        )

        graph = await store.get_node_neighborhood(
            workspace_id=uuid4(), start_node_id=a, depth=2
        )
        missing = await store.get_node_neighborhood(
            workspace_id=uuid4(), start_node_id=uuid4(), depth=2
        )

        assert graph is not None
        self.assertEqual([node.distance for node in graph.nodes], [0, 1, 2])
        self.assertIsNone(missing)


class FakeNeighborhoodStore:
    def __init__(self, graph) -> None:
        self.graph = graph
        self.call = None

    async def get_node_neighborhood(self, **kwargs):
        self.call = kwargs
        return self.graph


class NodeNeighborhoodApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides[require_workspace_member] = workspace_membership

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_requested_neighborhood(self) -> None:
        start, neighbor, edge_id = uuid4(), uuid4(), uuid4()
        graph_store = SimpleNodeNeighborhoodStore(
            nodes=[
                NeighborhoodNode(start, "person", "Alice"),
                NeighborhoodNode(neighbor, "person", "Bob"),
            ],
            edges=[NeighborhoodEdge(edge_id, start, neighbor, "knows", 0.7, 0.9)],
        )
        workspace_id = UUID("00000000-0000-0000-0000-000000000002")
        app.dependency_overrides[get_neighborhood_store] = lambda: graph_store

        response = TestClient(app).get(
            f"/api/v1/nodes/{start}/graph",
            params={"depth": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["start_node_id"], str(start))
        self.assertEqual(
            [(node["title"], node["distance"]) for node in response.json()["nodes"]],
            [("Alice", 0), ("Bob", 1)],
        )
        self.assertEqual(response.json()["edges"][0]["edge_id"], str(edge_id))

    def test_returns_only_displayable_type_specific_details(self) -> None:
        task, day = uuid4(), uuid4()
        graph_store = SimpleNodeNeighborhoodStore(
            nodes=[
                NeighborhoodNode(
                    task,
                    "task",
                    "Finish pitch deck",
                    type_specific={"task_status": "todo", "email": "x@example.com"},
                ),
                NeighborhoodNode(
                    day,
                    "date",
                    "June 1935",
                    type_specific={"date_value": "1935-06-01", "precision": "month"},
                ),
            ],
            edges=[NeighborhoodEdge(uuid4(), task, day, "related_to", 0.5, 0.5)],
        )
        app.dependency_overrides[get_neighborhood_store] = lambda: graph_store

        response = TestClient(app).get(f"/api/v1/nodes/{task}/graph")

        self.assertEqual(response.status_code, 200)
        task_node, date_node = response.json()["nodes"]
        self.assertEqual(
            task_node["details"],
            [{"name": "task_status", "label": "status", "kind": "enum", "value": "todo"}],
        )
        self.assertNotIn("type_specific", task_node)
        self.assertEqual(date_node["details"], [])

    def test_returns_aliases_without_title_or_external_id_duplicates(self) -> None:
        start = uuid4()
        graph_store = SimpleNodeNeighborhoodStore(
            nodes=[
                NeighborhoodNode(
                    start,
                    "person",
                    "Sarah Chen",
                    identifiers=["sarah chen", "Sarah", "sarahc@example.com", "Sar"],
                    external_ids=[
                        ("email", "sarahc@example.com"),
                        ("instagram", "sarahc"),
                    ],
                )
            ],
        )
        app.dependency_overrides[get_neighborhood_store] = lambda: graph_store

        response = TestClient(app).get(f"/api/v1/nodes/{start}/graph")

        self.assertEqual(response.status_code, 200)
        [node] = response.json()["nodes"]
        self.assertEqual(node["aliases"], ["Sarah", "Sar"])
        self.assertEqual(
            node["external_ids"],
            [
                {"provider": "email", "external_id": "sarahc@example.com"},
                {"provider": "instagram", "external_id": "sarahc"},
            ],
        )

    def test_returns_404_and_validates_depth(self) -> None:
        app.dependency_overrides[get_neighborhood_store] = (
            lambda: FakeNeighborhoodStore(None)
        )
        node_id = uuid4()
        missing = TestClient(app).get(f"/api/v1/nodes/{node_id}/graph")
        too_deep = TestClient(app).get(
            f"/api/v1/nodes/{node_id}/graph", params={"depth": 11}
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(too_deep.status_code, 422)


if __name__ == "__main__":
    unittest.main()
