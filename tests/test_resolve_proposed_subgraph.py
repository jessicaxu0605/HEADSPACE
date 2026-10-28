from __future__ import annotations

import unittest
from collections.abc import Iterable
from uuid import UUID, uuid4

from app.workflows.ingest.resolution.service import (
    ArchiveCanonicalEdge,
    CanonicalEdge,
    CanonicalNode,
    CreateNodeExternalId,
    CreateNodeIdentifier,
    NodeCandidate,
    RelationType,
    UpsertCanonicalEdge,
    UpdateCanonicalNodeTitle,
    resolve_proposed_subgraph,
)
from app.workflows.ingest.resolution.in_memory import (
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.ingest.models import (
    ProposedEdge,
    ProposedEvidence,
    ProposedEvidenceAnnotation,
    ProposedExternalId,
    ProposedGraph,
    ProposedNode,
)


DEFAULT_RELATIONS = {
    "related_to": RelationType(
        "related_to", "related_to", "General relationship.", is_symmetric=True
    ),
    "knows": RelationType(
        "knows",
        "knows",
        "The two people know each other.",
        is_symmetric=True,
        allowed_from_types=("person",),
        allowed_to_types=("person",),
    ),
    "has_birthday": RelationType(
        "has_birthday",
        "birthday_of",
        "The person's birthday.",
        is_exclusive=True,
        allowed_from_types=("person",),
        allowed_to_types=("date",),
    ),
}


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
        self.relations = {**DEFAULT_RELATIONS, **(relations or {})}

    async def find_node_candidates(self, *, workspace_id, proposed_node):
        return self.candidates.get(proposed_node.proposed_node_id, [])

    async def find_edges_between(self, *, workspace_id, node_id, other_node_id):
        return [
            edge
            for edge in self.edges
            if {edge.from_node_id, edge.to_node_id} == {node_id, other_node_id}
        ]

    async def resolve_relation_type(self, *, workspace_id, relation_type):
        for relation in self.relations.values():
            if relation_type == relation.name:
                return relation, False
            if relation_type == relation.inverse_name:
                return relation, not relation.is_symmetric
        return None

    async def list_relation_types(self, *, workspace_id):
        return list(self.relations.values())

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

    async def test_promotes_readable_location_title_and_keeps_old_title(self) -> None:
        hcmc = CanonicalNode(uuid4(), "location", "hcmc")
        graph = ProposedGraph(
            nodes=[
                ProposedNode(
                    "location_hcmc",
                    "location",
                    ["Ho Chi Minh City", "hcmc"],
                    "Ho Chi Minh City",
                )
            ]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=SimpleCanonicalGraphResolverStore(nodes=[hcmc]),
        )

        self.assertEqual(plan.node_bindings[0].node_id, hcmc.node_id)
        self.assertTrue(
            any(
                isinstance(action, UpdateCanonicalNodeTitle)
                and action.title == "Ho Chi Minh City"
                for action in plan.node_actions
            )
        )
        self.assertTrue(
            any(
                isinstance(action, CreateNodeIdentifier)
                and action.identifier_text == "hcmc"
                for action in plan.node_actions
            )
        )

    async def test_space_owner_alias_resolves_without_replacing_name(self) -> None:
        owner = CanonicalNode(
            uuid4(), "person", "Jessica", identifiers=["space owner"]
        )
        graph = ProposedGraph(
            nodes=[ProposedNode("me", "person", ["space owner"], "space owner")]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=SimpleCanonicalGraphResolverStore(nodes=[owner]),
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, owner.node_id)
        self.assertFalse(
            any(
                isinstance(action, UpdateCanonicalNodeTitle)
                for action in plan.node_actions
            )
        )

    async def test_email_local_part_resolves_person_and_promotes_title(self) -> None:
        email_person = CanonicalNode(uuid4(), "person", "andr3w.xu@gmail.com")
        graph = ProposedGraph(
            nodes=[ProposedNode("andrew", "person", ["Andrew Xu"], "Andrew Xu")]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=SimpleCanonicalGraphResolverStore(nodes=[email_person]),
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, email_person.node_id)
        self.assertTrue(
            any(
                isinstance(action, UpdateCanonicalNodeTitle)
                and action.title == "Andrew Xu"
                for action in plan.node_actions
            )
        )
        self.assertTrue(
            any(
                isinstance(action, CreateNodeExternalId)
                and action.provider == "email"
                and action.external_id == "andr3w.xu@gmail.com"
                for action in plan.node_actions
            )
        )

    async def test_resolves_named_person_without_merging_email_duplicate(self) -> None:
        named = CanonicalNode(uuid4(), "person", "Andrew Xu")
        email = CanonicalNode(uuid4(), "person", "andr3w.xu@gmail.com")
        graph = ProposedGraph(
            nodes=[ProposedNode("andrew", "person", ["Andrew Xu"], "Andrew Xu")]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=SimpleCanonicalGraphResolverStore(nodes=[named, email]),
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, named.node_id)

    async def test_weak_candidate_creates_possible_duplicate_review(self) -> None:
        candidate = CanonicalNode(uuid4(), "person", "Alicia")
        graph = ProposedGraph(
            nodes=[ProposedNode("alice", "person", ["Alice"], "Alice")]
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=FakeResolverStore(
                candidates={"alice": [NodeCandidate(candidate, "fuzzy", 0.60)]}
            ),
        )

        self.assertEqual(plan.node_bindings[0].outcome, "created")
        self.assertEqual(plan.repair_items[0].item_type, "possible_duplicate")

    async def test_two_shared_neighbors_promote_fuzzy_identity_match(self) -> None:
        alice = CanonicalNode(uuid4(), "person", "Alicia")
        acme = CanonicalNode(uuid4(), "concept", "Acme")
        singapore = CanonicalNode(uuid4(), "location", "Singapore")
        store = SimpleCanonicalGraphResolverStore(
            nodes=[alice, acme, singapore],
            relation_types=list(DEFAULT_RELATIONS.values()),
            edges=[
                CanonicalEdge(uuid4(), alice.node_id, acme.node_id, "related_to", 0.9, 0.5),
                CanonicalEdge(
                    uuid4(), alice.node_id, singapore.node_id, "related_to", 0.9, 0.5
                ),
            ],
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode("alice", "person", ["Alice"], "Alice"),
                ProposedNode("acme", "concept", ["Acme"], "Acme"),
                ProposedNode("singapore", "location", ["Singapore"], "Singapore"),
            ],
            edges=[
                ProposedEdge("e1", "alice", "acme", "related_to"),
                ProposedEdge("e2", "alice", "singapore", "related_to"),
            ],
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=store,
        )

        self.assertEqual(plan.node_bindings[0].outcome, "resolved")
        self.assertEqual(plan.node_bindings[0].node_id, alice.node_id)
        self.assertEqual(plan.node_bindings[0].match_score, 0.95)

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
                    details="  met at   the climbing gym ",
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
        self.assertEqual(edge.details, "met at the climbing gym")
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
        self.assertEqual(edge.operation, "specialize")
        self.assertEqual(edge.previous_relation_type, "unknown_type")
        self.assertAlmostEqual(edge.confidence, 0.7)
        self.assertAlmostEqual(edge.strength, 0.4)

    async def test_canonicalizes_inverse_relation_and_reverses_endpoints(self) -> None:
        person_id = UUID("00000000-0000-0000-0000-000000000001")
        date_id = UUID("00000000-0000-0000-0000-000000000002")
        store = FakeResolverStore(
            candidates={
                "date": [
                    NodeCandidate(
                        CanonicalNode(date_id, "date", "April 2"),
                        "normalized_title",
                        1.0,
                    )
                ],
                "person": [
                    NodeCandidate(
                        CanonicalNode(person_id, "person", "Alice"),
                        "normalized_title",
                        1.0,
                    )
                ],
            }
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode("date", "date", ["April 2"], "April 2"),
                ProposedNode("person", "person", ["Alice"], "Alice"),
            ],
            edges=[
                ProposedEdge(
                    "e1",
                    "date",
                    "person",
                    "birthday_of",
                    [ProposedEvidence("April 2 is Alice's birthday", 0.9)],
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
        self.assertEqual(edge.relation_type, "has_birthday")
        self.assertEqual(edge.from_node_id, person_id)
        self.assertEqual(edge.to_node_id, date_id)

    async def test_unsupported_relation_falls_back_to_related_and_is_flagged(self) -> None:
        alice_id = uuid4()
        bob_id = uuid4()
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
            }
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
                    "is_best_friends_with",
                    [ProposedEvidence("Alice is best friends with Bob", 0.9)],
                    details="best friends since primary school",
                )
            ],
        )

        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=store,
        )

        self.assertEqual(len(plan.edge_actions), 1)
        edge = plan.edge_actions[0]
        self.assertIsInstance(edge, UpsertCanonicalEdge)
        self.assertEqual(edge.relation_type, "related_to")
        self.assertEqual(edge.details, "best friends since primary school")
        self.assertEqual(
            {edge.from_node_id, edge.to_node_id},
            {alice_id, bob_id},
        )
        self.assertEqual(len(plan.repair_items), 1)
        review = plan.repair_items[0]
        self.assertEqual(review.item_type, "unsupported_relation_type")
        self.assertEqual(review.proposed_edge_id, "e1")
        self.assertEqual(
            review.details["proposed_relation_type"], "is_best_friends_with"
        )
        self.assertEqual(
            review.details["evidence"], ["Alice is best friends with Bob"]
        )
        self.assertEqual(
            review.details["details"], "best friends since primary school"
        )

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
            relations={
                "has_birthday": RelationType(
                    "has_birthday",
                    "birthday_of",
                    "The person's birthday.",
                    is_exclusive=True,
                    allowed_from_types=("person",),
                    allowed_to_types=("date",),
                )
            },
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


HIERARCHY_RELATIONS = {
    "related_to": DEFAULT_RELATIONS["related_to"],
    "knows": RelationType(
        "knows", "knows", "Know each other.", is_symmetric=True, parent_name="related_to"
    ),
    "friend_of": RelationType(
        "friend_of", "friend_of", "Friends.", is_symmetric=True, parent_name="knows"
    ),
    "colleague_of": RelationType(
        "colleague_of",
        "colleague_of",
        "Colleagues.",
        is_symmetric=True,
        parent_name="knows",
    ),
    "manages": RelationType(
        "manages", "managed_by", "Manages.", parent_name="knows"
    ),
    "located_in": RelationType(
        "located_in", "contains", "Located in.", parent_name="related_to"
    ),
    "occurs_at": RelationType(
        "occurs_at", "hosts", "Occurs at.", parent_name="located_in"
    ),
    "likes": RelationType("likes", "liked_by", "Likes.", parent_name="related_to"),
}


class RelationHierarchyResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.alice_id = UUID("00000000-0000-0000-0000-00000000000a")
        self.bob_id = UUID("00000000-0000-0000-0000-00000000000b")

    async def resolve(
        self,
        edges: list[CanonicalEdge],
        proposed: list[tuple[str, str, str]],
    ):
        store = FakeResolverStore(
            candidates={
                "alice": [
                    NodeCandidate(
                        CanonicalNode(self.alice_id, "person", "Alice"),
                        "normalized_title",
                        1.0,
                    )
                ],
                "bob": [
                    NodeCandidate(
                        CanonicalNode(self.bob_id, "person", "Bob"),
                        "normalized_title",
                        1.0,
                    )
                ],
            },
            edges=edges,
            relations=HIERARCHY_RELATIONS,
        )
        graph = ProposedGraph(
            nodes=[
                ProposedNode("alice", "person", ["Alice"], "Alice"),
                ProposedNode("bob", "person", ["Bob"], "Bob"),
            ],
            edges=[
                ProposedEdge(
                    f"e{index}",
                    from_id,
                    to_id,
                    relation_type,
                    [ProposedEvidence(f"evidence {index}", 0.5)],
                )
                for index, (from_id, relation_type, to_id) in enumerate(proposed)
            ],
        )
        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=store,
        )
        upserts = [a for a in plan.edge_actions if isinstance(a, UpsertCanonicalEdge)]
        archives = [a for a in plan.edge_actions if isinstance(a, ArchiveCanonicalEdge)]
        return upserts, archives

    def edge(self, relation_type: str, from_id: UUID, to_id: UUID) -> CanonicalEdge:
        return CanonicalEdge(uuid4(), from_id, to_id, relation_type, 0.5, 0.0)

    async def test_any_descendant_specializes_related_to(self) -> None:
        broad = self.edge("related_to", self.alice_id, self.bob_id)

        upserts, archives = await self.resolve([broad], [("alice", "friend_of", "bob")])

        self.assertEqual(archives, [])
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0].edge_id, broad.edge_id)
        self.assertEqual(upserts[0].operation, "specialize")
        self.assertEqual(upserts[0].relation_type, "friend_of")
        self.assertEqual(upserts[0].previous_relation_type, "related_to")
        self.assertAlmostEqual(upserts[0].confidence, 0.75)

    async def test_specializing_symmetric_edge_takes_asymmetric_direction(self) -> None:
        broad = self.edge("knows", self.alice_id, self.bob_id)

        upserts, _ = await self.resolve([broad], [("bob", "manages", "alice")])

        self.assertEqual(upserts[0].edge_id, broad.edge_id)
        self.assertEqual(upserts[0].operation, "specialize")
        self.assertEqual(
            (upserts[0].from_node_id, upserts[0].to_node_id),
            (self.bob_id, self.alice_id),
        )

    async def test_broader_relation_is_absorbed_by_specific_edge(self) -> None:
        specific = self.edge("friend_of", self.alice_id, self.bob_id)

        upserts, archives = await self.resolve(
            [specific], [("bob", "related_to", "alice")]
        )

        self.assertEqual(archives, [])
        self.assertEqual(upserts[0].edge_id, specific.edge_id)
        self.assertEqual(upserts[0].operation, "update")
        self.assertEqual(upserts[0].relation_type, "friend_of")
        self.assertEqual(upserts[0].absorbed_relation_type, "related_to")
        self.assertEqual(len(upserts[0].evidence), 1)

    async def test_symmetric_broader_relation_absorbs_regardless_of_direction(
        self,
    ) -> None:
        specific = self.edge("manages", self.bob_id, self.alice_id)

        upserts, _ = await self.resolve([specific], [("alice", "knows", "bob")])

        self.assertEqual(upserts[0].edge_id, specific.edge_id)
        self.assertEqual(upserts[0].relation_type, "manages")
        self.assertEqual(
            (upserts[0].from_node_id, upserts[0].to_node_id),
            (self.bob_id, self.alice_id),
        )

    async def test_opposite_asymmetric_direction_blocks_specialize_and_absorb(
        self,
    ) -> None:
        broad = self.edge("located_in", self.alice_id, self.bob_id)
        specific = self.edge("occurs_at", self.alice_id, self.bob_id)

        upgrade, _ = await self.resolve([broad], [("bob", "occurs_at", "alice")])
        absorb, _ = await self.resolve([specific], [("bob", "located_in", "alice")])

        self.assertEqual(upgrade[0].operation, "create")
        self.assertNotEqual(upgrade[0].edge_id, broad.edge_id)
        self.assertEqual(absorb[0].operation, "create")
        self.assertEqual(absorb[0].relation_type, "located_in")
        self.assertEqual(
            (absorb[0].from_node_id, absorb[0].to_node_id),
            (self.bob_id, self.alice_id),
        )

    async def test_unrelated_relation_types_create_separate_edges(self) -> None:
        friend = self.edge("friend_of", self.alice_id, self.bob_id)

        upserts, archives = await self.resolve(
            [friend], [("alice", "colleague_of", "bob"), ("alice", "likes", "bob")]
        )

        self.assertEqual(archives, [])
        self.assertEqual([u.operation for u in upserts], ["create", "create"])
        self.assertNotIn(friend.edge_id, {u.edge_id for u in upserts})

    async def test_specializing_archives_other_broader_edges(self) -> None:
        related = self.edge("related_to", self.alice_id, self.bob_id)
        knows = self.edge("knows", self.alice_id, self.bob_id)

        upserts, archives = await self.resolve(
            [related, knows], [("alice", "friend_of", "bob")]
        )

        self.assertEqual(upserts[0].edge_id, knows.edge_id)
        self.assertEqual(upserts[0].previous_relation_type, "knows")
        self.assertEqual(
            archives,
            [
                ArchiveCanonicalEdge(
                    related.edge_id, reason="superseded_by_specific_relation"
                )
            ],
        )

    async def test_edges_in_one_source_specialize_each_other(self) -> None:
        upserts, archives = await self.resolve(
            [],
            [
                ("alice", "related_to", "bob"),
                ("alice", "friend_of", "bob"),
                ("bob", "knows", "alice"),
            ],
        )

        self.assertEqual(archives, [])
        self.assertEqual(
            [u.operation for u in upserts], ["create", "specialize", "update"]
        )
        self.assertEqual(len({u.edge_id for u in upserts}), 1)
        self.assertEqual(
            [u.relation_type for u in upserts],
            ["related_to", "friend_of", "friend_of"],
        )
        self.assertEqual(upserts[2].absorbed_relation_type, "knows")


class RelationEndpointRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.event_id = uuid4()
        self.date_id = uuid4()
        self.store = FakeResolverStore(
            candidates={
                "party": [
                    NodeCandidate(
                        CanonicalNode(self.event_id, "event", "Party"),
                        "normalized_title",
                        1.0,
                    )
                ],
                "may3": [
                    NodeCandidate(
                        CanonicalNode(self.date_id, "date", "May 3"),
                        "normalized_title",
                        1.0,
                    )
                ],
            },
            relations={
                "occurs_on": RelationType(
                    "occurs_on",
                    "occurrence_date_of",
                    "Event date.",
                    allowed_from_types=("event",),
                    allowed_to_types=("date",),
                ),
                "scheduled_for": RelationType(
                    "scheduled_for",
                    "scheduled_date_of",
                    "Task date.",
                    allowed_from_types=("task",),
                    allowed_to_types=("date",),
                ),
            },
        )

    async def resolve(self, from_id: str, to_id: str, relation_type: str):
        graph = ProposedGraph(
            nodes=[
                ProposedNode("party", "event", ["Party"], "Party"),
                ProposedNode("may3", "date", ["May 3"], "May 3"),
            ],
            edges=[
                ProposedEdge(
                    "e1",
                    from_id,
                    to_id,
                    relation_type,
                    [ProposedEvidence("The party is on May 3.", 0.9)],
                )
            ],
        )
        return await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=graph,
            store=self.store,
        )

    async def test_wrong_relation_is_replaced_by_the_single_fitting_one(self) -> None:
        plan = await self.resolve("party", "may3", "scheduled_for")

        edge = plan.edge_actions[0]
        self.assertEqual(edge.relation_type, "occurs_on")
        self.assertEqual(
            (edge.from_node_id, edge.to_node_id), (self.event_id, self.date_id)
        )
        self.assertEqual(
            [item.item_type for item in plan.repair_items],
            ["repaired_relation_endpoints"],
        )
        self.assertEqual(plan.repair_items[0].severity, "low")

    async def test_backwards_relation_is_reversed(self) -> None:
        plan = await self.resolve("may3", "party", "occurs_on")

        edge = plan.edge_actions[0]
        self.assertEqual(edge.relation_type, "occurs_on")
        self.assertEqual(
            (edge.from_node_id, edge.to_node_id), (self.event_id, self.date_id)
        )


if __name__ == "__main__":
    unittest.main()
