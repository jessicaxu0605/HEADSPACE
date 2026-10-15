from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.embeddings import EmbeddingError
from app.persistence.postgres.node_search import PostgresNodeSearchStore
from app.persistence.postgres.projection_search import ProjectionMatch
from app.search.node_search import NodeSearchResult, merge_search_results


def _result(node_id, match_kind, score, reason, title="Sarah Chen", identifiers=()):
    return NodeSearchResult(
        node_id, "person", title, match_kind, score, list(identifiers), [reason]
    )


class MergeSearchResultsTests(unittest.TestCase):
    def test_node_found_by_both_mechanisms_outranks_single_list_leaders(self) -> None:
        # "Sarah from co-op": both Sarahs are weak fuzzy matches, only one has
        # a matching profile. A stronger vector-only hit still ranks below her.
        chen, kim, matcha = uuid4(), uuid4(), uuid4()
        merged = merge_search_results(
            [
                _result(kim, "fuzzy", 0.58, "similar to Sarah Kim", title="Sarah Kim"),
                _result(chen, "fuzzy", 0.57, "similar to Sarah Chen"),
            ],
            [
                _result(matcha, "profile", 0.80, "associated", title="Co-op"),
                _result(chen, "profile", 0.71, "associated with co-op"),
            ],
            limit=10,
        )

        self.assertEqual([item.node_id for item in merged], [chen, kim, matcha])
        # Chen ranked 2nd in both lists; lexical wins the tie for match_kind.
        self.assertEqual(merged[0].match_kind, "fuzzy")
        self.assertEqual(merged[0].score, 0.57)
        self.assertEqual(
            merged[0].reasons, ["similar to Sarah Chen", "associated with co-op"]
        )

    def test_exact_matches_are_pinned_above_fused_results(self) -> None:
        exact, both = uuid4(), uuid4()
        merged = merge_search_results(
            [
                _result(both, "fuzzy", 0.95, "similar", title="A"),
                _result(exact, "normalized_title", 0.98, "exact", title="Z"),
            ],
            [
                _result(both, "profile", 0.85, "associated", title="A"),
                _result(exact, "identity", 0.60, "describes", title="Z"),
            ],
            limit=10,
        )

        self.assertEqual([item.node_id for item in merged], [exact, both])
        self.assertEqual(merged[0].match_kind, "normalized_title")
        self.assertEqual(merged[0].reasons, ["exact", "describes"])

    def test_identity_and_profile_count_as_one_vector_vote(self) -> None:
        # Both projections of one node must not out-vote a node found by
        # fuzzy and vector search.
        double, fused = uuid4(), uuid4()
        merged = merge_search_results(
            [_result(fused, "fuzzy", 0.60, "similar", title="B")],
            [
                _result(double, "profile", 0.82, "associated", title="A"),
                _result(double, "identity", 0.80, "describes", title="A"),
                _result(fused, "identity", 0.70, "describes", title="B"),
            ],
            limit=10,
        )

        self.assertEqual([item.node_id for item in merged], [fused, double])
        self.assertEqual(merged[1].match_kind, "profile")
        self.assertEqual(merged[1].score, 0.82)

    def test_ranks_come_from_scores_not_input_order_and_limit_applies(self) -> None:
        low, high, extra = uuid4(), uuid4(), uuid4()
        merged = merge_search_results(
            [],
            [
                _result(low, "profile", 0.60, "low", title="A"),
                _result(high, "profile", 0.75, "high", title="B"),
                _result(extra, "profile", 0.56, "extra", title="C"),
            ],
            limit=2,
        )

        self.assertEqual([item.node_id for item in merged], [high, low])

    def test_vector_only_result_takes_identifiers_from_lexical_hit(self) -> None:
        node, other = uuid4(), uuid4()
        merged = merge_search_results(
            [
                _result(other, "fuzzy", 0.70, "similar", title="Other"),
                _result(node, "fuzzy", 0.55, "similar", identifiers=["Sarah"]),
            ],
            [_result(node, "profile", 0.90, "associated")],
            limit=5,
        )

        # Ranked 1st by vectors and 2nd lexically, so the vector match leads.
        self.assertEqual(merged[0].node_id, node)
        self.assertEqual(merged[0].match_kind, "profile")
        self.assertEqual(merged[0].identifiers, ["Sarah"])


class FailingEmbedder:
    model = "test-model"

    async def embed_query(self, text):
        raise EmbeddingError("Ollama is down")


class FixedEmbedder:
    model = "test-model"

    async def embed_query(self, text):
        return [1.0, 0.0]


class PostgresNodeSearchStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_lexical_when_embedding_fails(self) -> None:
        store = PostgresNodeSearchStore(embedder=FailingEmbedder())
        lexical = [_result(uuid4(), "fuzzy", 0.7, "similar title")]
        with patch.object(store, "_search_lexical", AsyncMock(return_value=lexical)):
            with self.assertLogs("app.persistence.postgres.node_search", "WARNING"):
                results = await store.search_nodes(
                    workspace_id=uuid4(),
                    query="Sarah",
                    node_type=None,
                    aliases=[],
                    external_ids=[],
                    limit=5,
                )

        self.assertEqual(results, lexical)

    async def test_semantic_matches_become_identity_and_profile_results(self) -> None:
        store = PostgresNodeSearchStore(embedder=FixedEmbedder())
        node_id = uuid4()
        nearest = AsyncMock(
            return_value=[
                ProjectionMatch(node_id, "profile", 0.74),
                ProjectionMatch(node_id, "identity", 0.61),
            ]
        )
        node_row = {"node_type": "person", "title": "Sarah Chen", "identifiers": ["Sarah"]}
        with (
            patch.object(store, "_search_lexical", AsyncMock(return_value=[])),
            patch(
                "app.persistence.postgres.node_search.nearest_node_projections", nearest
            ),
            patch(
                "app.persistence.postgres.node_search._load_nodes",
                AsyncMock(return_value={node_id: node_row}),
            ),
        ):
            results = await store.search_nodes(
                workspace_id=uuid4(),
                query="who likes matcha",
                node_type="person",
                aliases=[],
                external_ids=[],
                limit=5,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].match_kind, "profile")
        self.assertEqual(results[0].score, 0.74)
        self.assertEqual(results[0].identifiers, ["Sarah"])
        self.assertEqual(
            results[0].reasons,
            [
                "query matches what this person is associated with (0.74)",
                "query describes this person (0.61)",
            ],
        )
        kwargs = nearest.await_args.kwargs
        self.assertEqual(kwargs["projection_types"], ("identity", "profile"))
        self.assertEqual(kwargs["node_type"], "person")
        self.assertEqual(kwargs["embedding_model"], "test-model")


if __name__ == "__main__":
    unittest.main()
