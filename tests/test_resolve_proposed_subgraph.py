from __future__ import annotations

import unittest
from collections.abc import Iterable
from uuid import UUID, uuid4

from app.workflows.resolve_proposed_subgraph import (
    ArchiveCanonicalEdge,
    CanonicalEdge,
    CanonicalNode,
    NodeCandidate,
    RelationType,
    SimpleCanonicalGraphResolverStore,
    UpsertCanonicalEdge,
    resolve_proposed_subgraph,
)
from app.workflows.types import (
    ProposedEdge,
    ProposedEvidence,
    ProposedEvidenceAnnotation,
    ProposedExternalId,
    ProposedGraph,
    ProposedNode,
)


class FakeResolverStore:
    def __init__(
        self,
        *,
        candidates: dict[str, list[NodeCandidate]] | None = None,
        edges: Iterable[CanonicalEdge] = (),
        relations: dict[str, RelationType] | None = None,
    ) -> None:
        self.candidates = candidates or {}
        self.edges = list(edges)
        self.relations = relations or {}

    async def find_node_candidates(self, *, workspace_id, proposed_node):
        return self.candidates.get(proposed_node.proposed_node_id, [])

    async def find_edge(
        self, *, workspace_id, from_node_id, to_node_id, relation_type
    ):
        return next(
            (
                edge
                for edge in self.edges
                if edge.from_node_id == from_node_id
                and edge.to_node_id == to_node_id
                and edge.relation_type == relation_type
            ),
            None,
        )

    async def get_relation_type(self, *, workspace_id, relation_type):
        return self.relations.get(relation_type)

    async def find_active_outgoing_edges(
        self, *, workspace_id, from_node_id, relation_type
    ):
        return [
            edge
            for edge in self.edges
            if edge.from_node_id == from_node_id
            and edge.relation_type == relation_type
        ]


class ResolveProposedSubgraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_simple_store_finds_exact_and_fuzzy_candidates(self) -> None:
        jessica = CanonicalNode(
            uuid4(),
            "person",
            "Jessica Xu",
            identifiers=["Jess"],
            external_ids=[ProposedExternalId("slack", "U123")],
        )
        sarah = CanonicalNode(uuid4(), "person", "Sarah Chen")
        location = CanonicalNode(uuid4(), "location", "Sarah Chen")
        store = SimpleCanonicalGraphResolverStore(
            nodes=[jessica, sarah, location],
        )

        external_match = await store.find_node_candidates(
            workspace_id=uuid4(),
            proposed_node=ProposedNode(
                "n1",
                "person",
                ["J. Xu"],
                "J. Xu",
                external_ids=[ProposedExternalId("SLACK", "U123")],
            ),
        )
        identifier_match = await store.find_node_candidates(
            workspace_id=uuid4(),
            proposed_node=ProposedNode("n2", "person", ["jess"], "jess"),
        )
        fuzzy_match = await store.find_node_candidates(
            workspace_id=uuid4(),
            proposed_node=ProposedNode(
                "n3", "person", ["Sara Chen"], "Sara Chen"
            ),
        )

        self.assertEqual(external_match[0].match_kind, "external_id")
        self.assertEqual(external_match[0].node.node_id, jessica.node_id)
        self.assertEqual(identifier_match[0].match_kind, "identifier")
        self.assertEqual(identifier_match[0].node.node_id, jessica.node_id)
        self.assertEqual([item.node.node_id for item in fuzzy_match], [sarah.node_id])
        self.assertEqual(fuzzy_match[0].match_kind, "fuzzy")
        self.assertGreater(fuzzy_match[0].match_score, 0.92)

    async def test_simple_store_drives_fuzzy_node_resolution(self) -> None:
        sarah = CanonicalNode(uuid4(), "person", "Sarah Chen")
        graph = ProposedGraph(
            nodes=[ProposedNode("n1", "person", ["Sara Chen"], "Sara Chen")]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=SimpleCanonicalGraphResolverStore(nodes=[sarah]),
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, sarah.node_id)

    async def test_resolves_exact_node_and_plans_only_new_identity_data(self) -> None:
        workspace_id = uuid4()
        source_id = uuid4()
        canonical_id = uuid4()
        canonical = CanonicalNode(
            canonical_id,
            "person",
            "Jessica Xu",
            identifiers=["Jess"],
            external_ids=[ProposedExternalId("slack", "U123")],
        )
        store = FakeResolverStore(
            candidates={
                "n1": [NodeCandidate(canonical, "external_id", 1.0, ["Slack ID"])]
            }
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode(
                    "n1",
                    "person",
                    ["Jessica Xu", "Jess", "JX"],
                    "Jessica Xu",
                    external_ids=[
                        ProposedExternalId("slack", "U123"),
                        ProposedExternalId("gmail", "jessica@example.com"),
                    ],
                )
            ]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=workspace_id,
            source_id=source_id,
            proposed_graph=graph,
            store=store,
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, canonical_id)
        self.assertEqual(
            [(action.kind, getattr(action, "identifier_text", None)) for action in plan.node_actions],
            [("create_identifier", "JX"), ("create_external_id", None)],
        )

    async def test_ambiguous_identity_is_skipped_with_dependent_edge(self) -> None:
        first = CanonicalNode(uuid4(), "person", "Sarah Chen")
        second = CanonicalNode(uuid4(), "person", "Sarah Li")
        store = FakeResolverStore(
            candidates={
                "sarah": [
                    NodeCandidate(first, "fuzzy", 0.96),
                    NodeCandidate(second, "fuzzy", 0.94),
                ]
            }
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode("sarah", "person", ["Sarah"], "Sarah"),
                ProposedNode("acme", "concept", ["Acme"], "Acme"),
            ],
            edges=[ProposedEdge("e1", "sarah", "acme", "works_at")],
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=store,
        )

        self.assertEqual([item.outcome for item in plan.node_bindings], ["skipped", "created"])
        self.assertEqual(plan.edge_actions, [])
        self.assertEqual(
            [item.item_type for item in plan.repair_items],
            ["ambiguous_identity", "unresolved_edge_endpoint"],
        )

    async def test_creates_bound_edge_and_derives_scores_from_evidence(self) -> None:
        generated = iter(
            [
                UUID("00000000-0000-0000-0000-000000000001"),
                UUID("00000000-0000-0000-0000-000000000002"),
                UUID("00000000-0000-0000-0000-000000000003"),
            ]
        )
        source_id = uuid4()
        graph = ProposedGraph(
            nodes=[
                ProposedNode("n1", "person", ["Alice"], "Alice"),
                ProposedNode("n2", "person", ["Bob"], "Bob"),
            ],
            edges=[
                ProposedEdge(
                    "e1",
                    "n1",
                    "n2",
                    "knows",
                    [
                        ProposedEvidence(
                            "Alice knows Bob",
                            confidence=0.8,
                            strength_boost=0.5,
                            annotations=[ProposedEvidenceAnnotation(0, 5, "n1")],
                        ),
                        ProposedEvidence(
                            "Alice and Bob are friends",
                            confidence=0.5,
                            strength_boost=0.2,
                        ),
                    ],
                )
            ],
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=source_id,
            proposed_graph=graph,
            store=FakeResolverStore(),
            uuid_factory=lambda: next(generated),
        )

        edge = plan.edge_actions[0]
        self.assertIsInstance(edge, UpsertCanonicalEdge)
        self.assertEqual(edge.operation, "create")
        self.assertAlmostEqual(edge.confidence, 0.9)
        self.assertAlmostEqual(edge.strength, 0.46)
        self.assertEqual(edge.evidence[0].source_id, source_id)
        self.assertEqual(
            edge.evidence[0].annotations[0].node_id,
            UUID("00000000-0000-0000-0000-000000000001"),
        )

    async def test_upgrades_unknown_edge_and_combines_existing_scores(self) -> None:
        alice_id = uuid4()
        bob_id = uuid4()
        unknown = CanonicalEdge(uuid4(), alice_id, bob_id, "unknown_type", 0.4, 0.2)
        store = FakeResolverStore(
            candidates={
                "n1": [
                    NodeCandidate(
                        CanonicalNode(alice_id, "person", "Alice"),
                        "normalized_title",
                        1.0,
                    )
                ],
                "n2": [
                    NodeCandidate(
                        CanonicalNode(bob_id, "person", "Bob"),
                        "normalized_title",
                        1.0,
                    )
                ],
            },
            edges=[unknown],
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode("n1", "person", ["Alice"], "Alice"),
                ProposedNode("n2", "person", ["Bob"], "Bob"),
            ],
            edges=[
                ProposedEdge(
                    "e1",
                    "n1",
                    "n2",
                    "knows",
                    [ProposedEvidence("Alice knows Bob", 0.5, strength_boost=0.5)],
                )
            ],
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=store,
        )

        edge = plan.edge_actions[0]
        self.assertIsInstance(edge, UpsertCanonicalEdge)
        self.assertEqual(edge.edge_id, unknown.edge_id)
        self.assertEqual(edge.operation, "upgrade_unknown")
        self.assertEqual(edge.previous_relation_type, "unknown_type")
        self.assertAlmostEqual(edge.confidence, 0.7)
        self.assertAlmostEqual(edge.strength, 0.4)

    async def test_weak_exclusive_conflict_becomes_repair_item(self) -> None:
        person_id = uuid4()
        old_date_id = uuid4()
        new_date_id = uuid4()
        existing = CanonicalEdge(
            uuid4(), person_id, old_date_id, "has_birthday", 0.95, 0.5
        )
        store = FakeResolverStore(
            candidates={
                "person": [
                    NodeCandidate(
                        CanonicalNode(person_id, "person", "Alice"),
                        "normalized_title",
                        1.0,
                    )
                ],
                "birthday": [
                    NodeCandidate(
                        CanonicalNode(new_date_id, "date", "April 2"),
                        "normalized_title",
                        1.0,
                    )
                ],
            },
            edges=[existing],
            relations={"has_birthday": RelationType("has_birthday", is_exclusive=True)},
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode("person", "person", ["Alice"], "Alice"),
                ProposedNode("birthday", "date", ["April 2"], "April 2"),
            ],
            edges=[
                ProposedEdge(
                    "e1",
                    "person",
                    "birthday",
                    "has_birthday",
                    [ProposedEvidence("Alice's birthday might be April 2", 0.6)],
                )
            ],
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=store,
        )

        self.assertEqual(plan.edge_actions, [])
        self.assertEqual(plan.repair_items[0].item_type, "conflicting_exclusive_fact")
        self.assertFalse(
            any(isinstance(action, ArchiveCanonicalEdge) for action in plan.edge_actions)
        )


if __name__ == "__main__":
    unittest.main()
