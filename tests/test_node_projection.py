from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from app.search.node_projection import (
    NODE_PROJECTION_VERSION,
    NodeProjectionEngine,
    NodeProjectionSource,
    NodeSearchProjection,
    ProjectionEdge,
    ProjectionExternalId,
    ProjectionIdentifier,
    build_identity_projection,
    build_profile_projection,
)


def _edge(
    relation_type: str,
    connector: str,
    neighbor: str,
    *,
    outgoing: bool = True,
    strength: float = 0.5,
    confidence: float = 0.8,
    details: str | None = None,
    neighbor_type: str = "concept",
) -> ProjectionEdge:
    return ProjectionEdge(
        edge_id=uuid4(),
        relation_type=relation_type,
        connector=connector,
        outgoing=outgoing,
        neighbor_name=neighbor,
        neighbor_type=neighbor_type,
        strength=strength,
        confidence=confidence,
        details=details,
    )


def _sarah() -> NodeProjectionSource:
    return NodeProjectionSource(
        node_id=uuid4(),
        node_type="person",
        name="Sarah Chen",
        summary="Product designer Jessica met during co-op",
        type_specific={
            "pronouns": "she/her",
            "relationship_context": "co-op teammate",
        },
        identifiers=[
            ProjectionIdentifier("Sarah", confidence=0.95),
            ProjectionIdentifier("Sarah C", confidence=0.6),
            ProjectionIdentifier("sarah chen", confidence=0.9),
        ],
        external_ids=[
            ProjectionExternalId("google_contacts", "people/c123"),
            ProjectionExternalId("email", "sarah@example.com"),
            ProjectionExternalId("instagram", "sarahc"),
        ],
        edges=[
            _edge(
                "likes",
                "likes",
                "Matcha",
                strength=0.9,
                details="especially iced strawberry matcha lattes",
            ),
            _edge(
                "works_at",
                "works at",
                "Rippling",
                strength=0.7,
                neighbor_type="organization",
            ),
            _edge(
                "friend_of",
                "is friends with",
                "Jessica",
                outgoing=False,
                strength=0.4,
                neighbor_type="person",
            ),
        ],
    )


class NodeProjectionTextTests(unittest.TestCase):
    def test_person_identity_lists_aliases_and_abstract_providers(self) -> None:
        projection = build_identity_projection(_sarah())

        self.assertEqual(
            projection.embedding_text,
            "Sarah Chen. Also referred to as @sarahc, Sarah, Sarah C. "
            "Person from Google Contacts.",
        )
        # Raw contact values are searchable but stay out of the embedding.
        self.assertIn("sarah@example.com", projection.search_text)
        self.assertNotIn("people/c123", projection.search_text)

    def test_person_profile_uses_summary_facts_and_ranked_edges(self) -> None:
        projection = build_profile_projection(_sarah())

        self.assertEqual(
            projection.embedding_text,
            "Sarah Chen is a person. Product designer Jessica met during co-op. "
            "Relationship: co-op teammate. Pronouns: she/her. "
            "Sarah Chen likes Matcha (especially iced strawberry matcha lattes). "
            "Sarah Chen works at Rippling. "
            "Jessica is friends with Sarah Chen. "
            "Sarah Chen is connected to Matcha, Rippling, and Jessica.",
        )

    def test_identity_includes_strongest_identity_edges_only(self) -> None:
        source = NodeProjectionSource(
            node_id=uuid4(),
            node_type="organization",
            name="University of Waterloo",
            identifiers=[ProjectionIdentifier("UW"), ProjectionIdentifier("UWaterloo")],
            edges=[
                _edge("is_a", "is a", "University", strength=0.9),
                _edge("located_in", "is located in", "Waterloo", strength=0.8),
                _edge(
                    "studies_at",
                    "studies at",
                    "Jessica",
                    outgoing=False,
                    strength=1.0,
                ),
            ],
        )

        projection = build_identity_projection(source)

        self.assertEqual(
            projection.embedding_text,
            "University of Waterloo. Also referred to as UW, UWaterloo. "
            "Organization. University of Waterloo is a University. "
            "University of Waterloo is located in Waterloo.",
        )

    def test_date_and_task_render_type_specific_fields(self) -> None:
        date_node = NodeProjectionSource(
            node_id=uuid4(),
            node_type="date",
            name="Sarah's birthday",
            type_specific={"date_value": "2026-03-01", "precision": "month"},
        )
        task_node = NodeProjectionSource(
            node_id=uuid4(),
            node_type="task",
            name="Send visa documents",
            type_specific={
                "due_at": "2026-11-02T17:30:00+00:00",
                "urgency": "high",
                "task_status": "in_progress",
            },
        )

        self.assertEqual(
            build_identity_projection(date_node).embedding_text,
            "Sarah's birthday. Date for March 2026.",
        )
        self.assertEqual(
            build_identity_projection(task_node).embedding_text,
            "Send visa documents. Task due November 2, 2026 17:30.",
        )
        self.assertEqual(
            build_profile_projection(task_node).embedding_text,
            "Send visa documents is a task. Status: in progress. High urgency. "
            "Due November 2, 2026 17:30.",
        )

    def test_profile_caps_edges_and_truncates_details(self) -> None:
        source = NodeProjectionSource(
            node_id=uuid4(),
            node_type="concept",
            name="Matcha",
            edges=[
                _edge("about", "is about", f"Note {index:02d}", details="x " * 200)
                for index in range(30)
            ],
        )

        text = build_profile_projection(source).embedding_text

        self.assertEqual(text.count("Matcha is about Note"), 15)
        self.assertLess(len(text), 4000)


class RecordingEmbedder:
    model = "test-model"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[float(index), 1.0] for index, _ in enumerate(texts)]


class MemoryProjectionStore:
    def __init__(self, sources: dict[UUID, NodeProjectionSource]) -> None:
        self.sources = sources
        self.saved: dict[tuple[UUID, str], NodeSearchProjection] = {}
        self.deleted: list[UUID] = []

    async def load_node_projection_source(self, *, workspace_id, node_id):
        return self.sources.get(node_id)

    async def get_projection_hashes(
        self, *, workspace_id, node_id, embedding_model, projection_version
    ):
        return {
            projection_type: projection.source_hash
            for (saved_node_id, projection_type), projection in self.saved.items()
            if saved_node_id == node_id
            and projection.embedding_model == embedding_model
            and projection.projection_version == projection_version
        }

    async def save_node_projections(self, *, workspace_id, projections):
        for projection in projections:
            self.saved[(projection.node_id, projection.projection_type)] = projection

    async def delete_node_projections(self, *, workspace_id, node_id):
        self.deleted.append(node_id)


class NodeProjectionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_embeds_both_projections_then_skips_unchanged_text(self) -> None:
        source = _sarah()
        store = MemoryProjectionStore({source.node_id: source})
        embedder = RecordingEmbedder()
        engine = NodeProjectionEngine(store=store, embedder=embedder)
        workspace_id = uuid4()

        written = await engine.rebuild_node(
            workspace_id=workspace_id, node_id=source.node_id
        )
        self.assertEqual(
            [projection.projection_type for projection in written],
            ["identity", "profile"],
        )
        self.assertEqual(len(embedder.calls), 1)
        self.assertEqual(written[0].projection_version, NODE_PROJECTION_VERSION)
        self.assertEqual(written[0].embedding_model, "test-model")

        rewritten = await engine.rebuild_node(
            workspace_id=workspace_id, node_id=source.node_id
        )
        self.assertEqual(rewritten, [])
        self.assertEqual(len(embedder.calls), 1)

    async def test_reembeds_only_the_projection_whose_text_changed(self) -> None:
        source = _sarah()
        store = MemoryProjectionStore({source.node_id: source})
        embedder = RecordingEmbedder()
        engine = NodeProjectionEngine(store=store, embedder=embedder)
        workspace_id = uuid4()
        await engine.rebuild_node(workspace_id=workspace_id, node_id=source.node_id)

        store.sources[source.node_id] = NodeProjectionSource(
            **{
                **{name: getattr(source, name) for name in source.__slots__},
                "summary": "Now a design lead at Rippling",
            }
        )
        written = await engine.rebuild_node(
            workspace_id=workspace_id, node_id=source.node_id
        )

        self.assertEqual([projection.projection_type for projection in written], ["profile"])
        self.assertEqual(len(embedder.calls[-1]), 1)

    async def test_missing_node_deletes_projections(self) -> None:
        store = MemoryProjectionStore({})
        engine = NodeProjectionEngine(store=store, embedder=RecordingEmbedder())
        node_id = uuid4()

        written = await engine.rebuild_node(workspace_id=uuid4(), node_id=node_id)

        self.assertEqual(written, [])
        self.assertEqual(store.deleted, [node_id])


if __name__ == "__main__":
    unittest.main()
