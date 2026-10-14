from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app, get_edge_detail_store
from app.graph.queries.edge_detail import (
    EdgeDetail,
    EdgeEndpointNode,
    EdgeEvidence,
    EdgeEvidenceAnnotation,
    EdgeEvidenceSource,
)


class FakeEdgeDetailStore:
    def __init__(self, edge: EdgeDetail | None) -> None:
        self.edge = edge
        self.calls = []

    async def get_edge_detail(self, **kwargs):
        self.calls.append(kwargs)
        return self.edge


def make_edge() -> EdgeDetail:
    sarah = EdgeEndpointNode(uuid4(), "person", "Sarah", "A friend from work")
    matcha = EdgeEndpointNode(uuid4(), "concept", "Matcha")
    ingested_at = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    return EdgeDetail(
        edge_id=uuid4(),
        relation_type="likes",
        inverse_relation_type="liked_by",
        relation_description="Subject likes or enjoys object.",
        connector="likes",
        is_symmetric=False,
        details="especially strawberry matcha lattes; usually orders it iced",
        strength=0.4,
        confidence=0.95,
        status="active",
        valid_from=None,
        valid_to=None,
        created_at=ingested_at,
        updated_at=ingested_at,
        deleted_at=None,
        deleted_reason=None,
        from_node=sarah,
        to_node=matcha,
        evidence=[
            EdgeEvidence(
                evidence_id=uuid4(),
                raw_content="Sarah loves strawberry matcha lattes",
                confidence=0.9,
                strength_boost=0.5,
                is_verbatim=False,
                created_at=ingested_at,
                source=EdgeEvidenceSource(
                    source_id=uuid4(),
                    source_type="manual_entry",
                    source_metadata={},
                    created_at=ingested_at,
                ),
                annotations=[EdgeEvidenceAnnotation(0, 5, sarah.node_id)],
            )
        ],
    )


class EdgeDetailApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_returns_edge_endpoints_and_evidence_with_ingest_date(self) -> None:
        edge = make_edge()
        store = FakeEdgeDetailStore(edge)
        app.dependency_overrides[get_edge_detail_store] = lambda: store

        response = TestClient(app).get(f"/api/v1/edges/{edge.edge_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["edge_id"], str(edge.edge_id))
        self.assertEqual(body["relation_type"], "likes")
        self.assertEqual(body["connector"], "likes")
        self.assertEqual(body["details"], edge.details)
        self.assertEqual(body["from_node"]["title"], "Sarah")
        self.assertEqual(body["to_node"]["title"], "Matcha")
        self.assertIsNone(body["to_node"]["summary"])
        evidence = body["evidence"][0]
        self.assertEqual(evidence["raw_content"], "Sarah loves strawberry matcha lattes")
        self.assertFalse(evidence["is_verbatim"])
        self.assertEqual(
            evidence["source"]["created_at"], "2026-10-01T09:30:00Z"
        )
        self.assertEqual(
            evidence["annotations"][0]["node_id"], str(edge.from_node.node_id)
        )
        self.assertEqual(
            store.calls,
            [
                {
                    "workspace_id": UUID("00000000-0000-0000-0000-000000000002"),
                    "edge_id": edge.edge_id,
                }
            ],
        )

    def test_missing_edge_returns_404(self) -> None:
        app.dependency_overrides[get_edge_detail_store] = (
            lambda: FakeEdgeDetailStore(None)
        )

        response = TestClient(app).get(f"/api/v1/edges/{uuid4()}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Edge not found.")

    def test_rejects_malformed_edge_id(self) -> None:
        response = TestClient(app).get("/api/v1/edges/not-a-uuid")

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
