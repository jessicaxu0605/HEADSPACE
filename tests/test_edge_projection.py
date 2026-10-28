from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from app.workflows.rebuild_projections.edge import (
    EDGE_PROJECTION_VERSION,
    EdgeProjectionEngine,
    EdgeProjectionSource,
    ProjectionAnnotation,
    ProjectionEvidence,
    build_edge_projection_text,
    render_evidence,
)


def _likes_matcha() -> EdgeProjectionSource:
    return EdgeProjectionSource(
        edge_id=uuid4(),
        relation_type="likes",
        connector="likes",
        from_name="Sarah Chen",
        from_type="person",
        to_name="Matcha",
        to_type="concept",
        relation_description=(
            "The subject likes or enjoys the object. Use prefers for choices."
        ),
        parent_relation="related_to",
        details="especially strawberry matcha lattes; usually iced",
        evidence=[
            ProjectionEvidence(
                "She said she loved the strawberry matcha latte there.",
                confidence=0.9,
                annotations=[
                    ProjectionAnnotation(9, 3, "Sarah Chen"),
                    ProjectionAnnotation(0, 3, "Sarah Chen"),
                    ProjectionAnnotation(47, 5, "Café Kitsuné"),
                ],
            ),
            ProjectionEvidence("Sarah orders matcha every time.", confidence=0.6),
        ],
    )


class EdgeProjectionTextTests(unittest.TestCase):
    def test_builds_fact_relation_detail_and_evidence(self) -> None:
        text = build_edge_projection_text(_likes_matcha())

        self.assertEqual(
            text.embedding_text,
            "Sarah Chen likes Matcha. "
            "A likes relation between a person and a concept: "
            "The subject likes or enjoys the object. "
            "Detail: especially strawberry matcha lattes; usually iced. "
            'Evidence: "Sarah Chen said Sarah Chen loved the strawberry matcha '
            'latte Café Kitsuné." "Sarah orders matcha every time."',
        )
        self.assertEqual(
            text.search_text.split(" | ")[:4],
            [
                "Sarah Chen",
                "Matcha",
                "likes",
                "especially strawberry matcha lattes; usually iced",
            ],
        )

    def test_generic_relation_skips_catalog_guidance(self) -> None:
        source = replace(
            _likes_matcha(),
            relation_type="related_to",
            connector="is connected to",
            relation_description="Fallback: the two nodes have a meaningful relationship.",
            parent_relation=None,
            evidence=[],
        )

        text = build_edge_projection_text(source)

        self.assertEqual(
            text.embedding_text,
            "Sarah Chen is connected to Matcha. "
            "A related to relation between a person and a concept. "
            "Detail: especially strawberry matcha lattes; usually iced.",
        )

    def test_names_specific_parent_and_validity_window(self) -> None:
        source = EdgeProjectionSource(
            edge_id=uuid4(),
            relation_type="colleague_of",
            connector="was a colleague of",
            from_name="Jessica",
            from_type="person",
            to_name="Sarah Chen",
            to_type="person",
            relation_description="The two people work together.",
            parent_relation="knows",
            valid_from=datetime(2025, 1, 6, tzinfo=timezone.utc),
            valid_to=datetime(2025, 4, 25, tzinfo=timezone.utc),
        )

        self.assertEqual(
            build_edge_projection_text(source).embedding_text,
            "Jessica was a colleague of Sarah Chen. "
            "A colleague of relation between a person and a person, a kind of "
            "knows: The two people work together. "
            "From January 6, 2025 until April 25, 2025.",
        )

    def test_evidence_is_ranked_deduplicated_and_capped(self) -> None:
        source = replace(
            _likes_matcha(),
            evidence=[
                ProjectionEvidence(f"Quote {index}.", confidence=index / 10)
                for index in range(6)
            ]
            + [ProjectionEvidence("quote 5.", confidence=0.1)],
        )

        text = build_edge_projection_text(source).embedding_text

        self.assertTrue(text.endswith('Evidence: "Quote 5." "Quote 4." "Quote 3."'))

    def test_render_evidence_skips_overlapping_annotations(self) -> None:
        evidence = ProjectionEvidence(
            "She met him.",
            annotations=[
                ProjectionAnnotation(0, 3, "Sarah"),
                ProjectionAnnotation(1, 4, "Ignored"),
                ProjectionAnnotation(8, 3, "Tom"),
                ProjectionAnnotation(10, 10, "Out of range"),
            ],
        )

        self.assertEqual(render_evidence(evidence), "Sarah met Tom.")


class RecordingEmbedder:
    model = "test-model"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


class MemoryEdgeStore:
    def __init__(self, sources) -> None:
        self.sources = sources
        self.saved = {}
        self.deleted = []

    async def load_edge_projection_source(self, *, workspace_id, edge_id):
        return self.sources.get(edge_id)

    async def get_edge_projection_hash(
        self, *, workspace_id, edge_id, embedding_model, projection_version
    ):
        projection = self.saved.get(edge_id)
        if (
            projection is None
            or projection.embedding_model != embedding_model
            or projection.projection_version != projection_version
        ):
            return None
        return projection.source_hash

    async def save_edge_projection(self, *, workspace_id, projection):
        self.saved[projection.edge_id] = projection

    async def delete_edge_projections(self, *, workspace_id, edge_id):
        self.deleted.append(edge_id)


class EdgeProjectionEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_embeds_once_until_text_changes(self) -> None:
        source = _likes_matcha()
        store = MemoryEdgeStore({source.edge_id: source})
        embedder = RecordingEmbedder()
        engine = EdgeProjectionEngine(store=store, embedder=embedder)
        workspace_id = uuid4()

        written = await engine.rebuild_edge(
            workspace_id=workspace_id, edge_id=source.edge_id
        )
        self.assertIsNotNone(written)
        self.assertEqual(written.projection_version, EDGE_PROJECTION_VERSION)
        self.assertEqual(embedder.calls, [[written.embedding_text]])

        self.assertIsNone(
            await engine.rebuild_edge(workspace_id=workspace_id, edge_id=source.edge_id)
        )
        self.assertEqual(len(embedder.calls), 1)

        store.sources[source.edge_id] = replace(source, details="hot in winter")
        rewritten = await engine.rebuild_edge(
            workspace_id=workspace_id, edge_id=source.edge_id
        )
        self.assertIn("Detail: hot in winter.", rewritten.embedding_text)
        self.assertEqual(len(embedder.calls), 2)

    async def test_missing_edge_deletes_projection(self) -> None:
        store = MemoryEdgeStore({})
        engine = EdgeProjectionEngine(store=store, embedder=RecordingEmbedder())
        edge_id = uuid4()

        self.assertIsNone(await engine.rebuild_edge(workspace_id=uuid4(), edge_id=edge_id))
        self.assertEqual(store.deleted, [edge_id])


if __name__ == "__main__":
    unittest.main()
