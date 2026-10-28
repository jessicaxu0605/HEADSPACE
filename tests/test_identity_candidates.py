from __future__ import annotations

import unittest
from uuid import uuid4

from app.workflows.ingest.resolution.identity_candidates import (
    IDENTITY_EMBEDDING_MAX_SCORE,
    identity_embedding_candidate,
    merge_identity_candidates,
    needs_identity_embedding,
    proposed_identity_text,
)
from app.workflows.ingest.resolution.service import (
    CanonicalNode,
    NodeCandidate,
    resolve_proposed_subgraph,
)
from app.workflows.ingest.models import ProposedExternalId, ProposedGraph, ProposedNode
from tests.test_resolve_proposed_subgraph import FakeResolverStore


def _proposed(node_type="person", name="Sarah Chen", **kwargs) -> ProposedNode:
    return ProposedNode("n1", node_type, kwargs.pop("names", [name]), name, **kwargs)


class IdentityCandidateTests(unittest.TestCase):
    def test_proposed_text_matches_identity_projection_format(self) -> None:
        node = _proposed(
            names=["Sarah Chen", "Sarah C"],
            external_ids=[
                ProposedExternalId("google_contacts", "people/c1"),
                ProposedExternalId("instagram", "sarahc"),
            ],
        )

        self.assertEqual(
            proposed_identity_text(node),
            "Sarah Chen. Also referred to as @sarahc, Sarah C. "
            "Person from Google Contacts.",
        )

    def test_candidate_is_capped_below_auto_resolve(self) -> None:
        canonical = CanonicalNode(uuid4(), "person", "Sarah C")

        candidate = identity_embedding_candidate(_proposed(), canonical, 0.97)

        self.assertEqual(candidate.match_kind, "identity_embedding")
        self.assertEqual(candidate.match_score, IDENTITY_EMBEDDING_MAX_SCORE)
        self.assertEqual(
            candidate.reasons, ["identity description is semantically similar (0.97)"]
        )

    def test_hard_identity_rules_block_candidates(self) -> None:
        march_3 = {"date_value": "2026-03-03", "precision": "day"}
        march_4 = {"date_value": "2026-03-04", "precision": "day"}

        self.assertIsNone(
            identity_embedding_candidate(
                _proposed(), CanonicalNode(uuid4(), "person", "Sarah"), 0.80
            )
        )
        self.assertIsNone(
            identity_embedding_candidate(
                _proposed(), CanonicalNode(uuid4(), "location", "Sarah Chen"), 0.95
            )
        )
        self.assertIsNone(
            identity_embedding_candidate(
                _proposed("date", "Sarah's party", type_specific=march_3),
                CanonicalNode(uuid4(), "date", "Party", type_specific=march_4),
                0.95,
            )
        )

    def test_embedding_runs_only_without_exact_lexical_match(self) -> None:
        node = CanonicalNode(uuid4(), "person", "Sarah")

        self.assertTrue(needs_identity_embedding([]))
        self.assertTrue(needs_identity_embedding([NodeCandidate(node, "fuzzy", 0.7)]))
        self.assertFalse(
            needs_identity_embedding([NodeCandidate(node, "identifier", 0.99)])
        )

    def test_merge_keeps_stronger_signal_per_node(self) -> None:
        shared = CanonicalNode(uuid4(), "person", "Sarah C")
        only_embedding = CanonicalNode(uuid4(), "person", "S. Chen")

        merged = merge_identity_candidates(
            [NodeCandidate(shared, "fuzzy", 0.70, ["lexically similar"])],
            [
                NodeCandidate(shared, "identity_embedding", 0.88, ["semantic"]),
                NodeCandidate(only_embedding, "identity_embedding", 0.86, ["semantic"]),
            ],
        )

        self.assertEqual(
            [(item.node.node_id, item.match_kind) for item in merged],
            [
                (shared.node_id, "identity_embedding"),
                (only_embedding.node_id, "identity_embedding"),
            ],
        )
        self.assertEqual(merged[0].reasons, ["semantic", "lexically similar"])


class IdentityEmbeddingResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_candidate_alone_creates_with_review_item(self) -> None:
        canonical = CanonicalNode(uuid4(), "person", "Sarah C")
        candidate = identity_embedding_candidate(_proposed(), canonical, 0.99)

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=ProposedGraph(nodes=[_proposed()]),
            store=FakeResolverStore(candidates={"n1": [candidate]}),
        )

        self.assertEqual(plan.node_bindings[0].outcome, "created")
        self.assertEqual(plan.repair_items[0].item_type, "possible_duplicate")


if __name__ == "__main__":
    unittest.main()
