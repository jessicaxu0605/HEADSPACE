"""Plan canonical graph changes for an extracted proposed subgraph.

This module is deliberately persistence-agnostic. Candidate retrieval and reads of
the canonical graph belong to ``CanonicalGraphResolverStore``; this workflow makes
conservative resolution decisions and returns an ordered, inspectable transaction
plan for Phase 3 to apply atomically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from math import prod
import re
from typing import Literal, Protocol, TypeAlias
from uuid import UUID, uuid4

from app.workflows.relation_types import (
    RelationDefinition,
    canonicalize_relation_type,
    relation_ancestors,
)
from app.workflows.types import (
    JsonObject,
    NodeType,
    ProposedEdge,
    ProposedEvidence,
    ProposedExternalId,
    ProposedGraph,
    ProposedNode,
)


NodeMatchKind: TypeAlias = Literal[
    "external_id",
    "identifier",
    "normalized_title",
    "fuzzy",
]

# TODO: tune these values
@dataclass(frozen=True, slots=True)
class ResolutionPolicy:
    fuzzy_resolve_threshold: float = 0.92
    ambiguous_score_gap: float = 0.08
    medium_match_threshold: float = 0.65
    review_match_threshold: float = 0.50
    identifier_similarity_threshold: float = 0.65
    merge_match_threshold: float = 0.96
    one_shared_neighbor_score: float = 0.75
    two_shared_neighbors_score: float = 0.95
    exclusive_replacement_threshold: float = 0.90
    unknown_relation_type: str = "unknown_type"

@dataclass(frozen=True, slots=True)
class CanonicalNode:
    node_id: UUID
    node_type: NodeType | str
    title: str
    identifiers: list[str] = field(default_factory=list)
    external_ids: list[ProposedExternalId] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NodeCandidate:
    """A store-produced candidate, including retrieval and scoring signals."""

    node: CanonicalNode
    match_kind: NodeMatchKind
    match_score: float
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CanonicalEdge:
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    confidence: float
    strength: float


RelationType = RelationDefinition


class CanonicalGraphResolverStore(Protocol):
    """Read surface needed by resolution; implementations may use SQL/search/LLMs."""

    async def find_node_candidates(
        self,
        *,
        workspace_id: UUID,
        proposed_node: ProposedNode,
    ) -> list[NodeCandidate]: ...

    async def find_edges_between(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
        other_node_id: UUID,
    ) -> list[CanonicalEdge]:
        """Return active edges between the two nodes, in either direction."""
        ...

    async def resolve_relation_type(
        self,
        *,
        workspace_id: UUID,
        relation_type: str,
    ) -> tuple[RelationType, bool] | None: ...

    async def list_relation_types(
        self,
        *,
        workspace_id: UUID,
    ) -> list[RelationType]: ...

    async def find_active_outgoing_edges(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        relation_type: str,
    ) -> list[CanonicalEdge]: ...

    async def find_neighbor_node_ids(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
    ) -> set[UUID]: ...


class NodeCandidateSearchStore(Protocol):
    """Search canonical nodes for an interactive user query."""

    async def search_node_candidates(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        aliases: list[str],
        external_ids: list[ProposedExternalId],
        limit: int,
    ) -> list[NodeCandidate]: ...


class SimpleCanonicalGraphResolverStore:
    """Small lexical resolver over an in-memory canonical graph snapshot.

    This is the baseline implementation for the prototype. A PostgreSQL adapter can
    load the same records and delegate candidate scoring here until search
    projections or identity embeddings are available.
    """

    def __init__(
        self,
        *,
        nodes: list[CanonicalNode] | None = None,
        edges: list[CanonicalEdge] | None = None,
        relation_types: list[RelationType] | None = None,
        fuzzy_candidate_threshold: float = 0.50,
    ) -> None:
        if not 0.0 <= fuzzy_candidate_threshold <= 1.0:
            raise ValueError("fuzzy_candidate_threshold must be between 0 and 1.")
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])
        self.relation_types = {
            relation_type.name: relation_type
            for relation_type in relation_types or []
        }
        self.fuzzy_candidate_threshold = fuzzy_candidate_threshold

    async def find_node_candidates(
        self,
        *,
        workspace_id: UUID,
        proposed_node: ProposedNode,
    ) -> list[NodeCandidate]:
        del workspace_id  # The supplied snapshot is already workspace-scoped.
        candidates = [
            candidate
            for node in self.nodes
            if (candidate := self._candidate_for(proposed_node, node)) is not None
        ]
        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate.match_score,
                candidate.node.title.casefold(),
                str(candidate.node.node_id),
            ),
        )

    async def find_edges_between(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
        other_node_id: UUID,
    ) -> list[CanonicalEdge]:
        del workspace_id
        return [
            edge
            for edge in self.edges
            if {edge.from_node_id, edge.to_node_id} == {node_id, other_node_id}
        ]

    async def resolve_relation_type(
        self,
        *,
        workspace_id: UUID,
        relation_type: str,
    ) -> tuple[RelationType, bool] | None:
        del workspace_id
        return canonicalize_relation_type(
            relation_type,
            list(self.relation_types.values()),
        )

    async def list_relation_types(
        self,
        *,
        workspace_id: UUID,
    ) -> list[RelationType]:
        del workspace_id
        return list(self.relation_types.values())

    async def find_active_outgoing_edges(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        relation_type: str,
    ) -> list[CanonicalEdge]:
        del workspace_id
        return [
            edge
            for edge in self.edges
            if edge.from_node_id == from_node_id
            and edge.relation_type == relation_type
        ]

    async def find_neighbor_node_ids(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
    ) -> set[UUID]:
        del workspace_id
        return {
            edge.to_node_id if edge.from_node_id == node_id else edge.from_node_id
            for edge in self.edges
            if edge.from_node_id == node_id or edge.to_node_id == node_id
        }

    def _candidate_for(
        self,
        proposed_node: ProposedNode,
        canonical_node: CanonicalNode,
    ) -> NodeCandidate | None:
        proposed_external_ids = {
            (item.provider.casefold(), item.external_id)
            for item in proposed_node.external_ids
        }
        canonical_external_ids = {
            (item.provider.casefold(), item.external_id)
            for item in canonical_node.external_ids
        }
        matching_external_ids = proposed_external_ids & canonical_external_ids
        if matching_external_ids:
            provider, _ = sorted(matching_external_ids)[0]
            return NodeCandidate(
                canonical_node,
                "external_id",
                1.0,
                [f"matching {provider} external ID"],
            )

        if canonical_node.node_type != proposed_node.node_type:
            return None

        proposed_names = {
            _normalize(name)
            for name in [
                proposed_node.primary_reference_name,
                *proposed_node.names,
            ]
            if _normalize(name)
        }
        identifiers_by_normalized = {
            _normalize(identifier): identifier
            for identifier in canonical_node.identifiers
        }
        matching_identifiers = proposed_names & identifiers_by_normalized.keys()
        if matching_identifiers:
            matched = sorted(matching_identifiers)[0]
            return NodeCandidate(
                canonical_node,
                "identifier",
                0.99,
                [f"exact identifier match: {identifiers_by_normalized[matched]!r}"],
            )

        normalized_title = _normalize(canonical_node.title)
        if normalized_title in proposed_names:
            return NodeCandidate(
                canonical_node,
                "normalized_title",
                0.98,
                [f"exact normalized title match: {canonical_node.title!r}"],
            )

        email_identity = _email_identity_match(proposed_names, canonical_node)
        if email_identity is not None:
            return NodeCandidate(
                canonical_node,
                "fuzzy",
                0.97,
                [email_identity],
            )

        canonical_names = [canonical_node.title, *canonical_node.identifiers]
        comparisons = [
            (
                SequenceMatcher(None, proposed_name, _normalize(canonical_name)).ratio(),
                proposed_name,
                canonical_name,
            )
            for proposed_name in proposed_names
            for canonical_name in canonical_names
            if _normalize(canonical_name)
        ]
        if not comparisons:
            return None
        score, proposed_name, canonical_name = max(comparisons)
        if score < self.fuzzy_candidate_threshold:
            return None
        return NodeCandidate(
            canonical_node,
            "fuzzy",
            score,
            [f"{proposed_name!r} is lexically similar to {canonical_name!r}"],
        )


@dataclass(frozen=True, slots=True)
class ResolveRepairCandidate:
    node_id: UUID
    title: str
    node_type: str
    match_score: float
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResolveRepairItem:
    item_type: Literal[
        "ambiguous_identity",
        "possible_duplicate",
        "unresolved_edge_endpoint",
        "conflicting_exclusive_fact",
        "unsupported_relation_type",
        "invalid_relation_endpoints",
    ]
    source_id: UUID
    severity: Literal["low", "medium", "high"]
    message: str
    proposed_node_id: str | None = None
    proposed_edge_id: str | None = None
    candidates: list[ResolveRepairCandidate] = field(default_factory=list)
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NodeBinding:
    proposed_node_id: str
    node_id: UUID | None
    outcome: Literal["resolved", "created", "skipped"]
    match_score: float | None = None


@dataclass(frozen=True, slots=True)
class CreateCanonicalNode:
    node_id: UUID
    node_type: NodeType
    title: str
    summary: str | None
    type_specific: JsonObject | None
    idempotency_key: str
    duplicate_candidates: list[ResolveRepairCandidate] = field(default_factory=list)
    kind: Literal["create_node"] = "create_node"


@dataclass(frozen=True, slots=True)
class CreateNodeIdentifier:
    node_id: UUID
    identifier_text: str
    kind: Literal["create_identifier"] = "create_identifier"


@dataclass(frozen=True, slots=True)
class CreateNodeExternalId:
    node_id: UUID
    provider: str
    external_id: str
    kind: Literal["create_external_id"] = "create_external_id"


@dataclass(frozen=True, slots=True)
class UpdateCanonicalNodeTitle:
    node_id: UUID
    title: str
    kind: Literal["update_node_title"] = "update_node_title"


@dataclass(frozen=True, slots=True)
class MergeCanonicalNodes:
    from_node_id: UUID
    into_node_id: UUID
    reason: str
    kind: Literal["merge_nodes"] = "merge_nodes"


NodePlanAction: TypeAlias = (
    CreateCanonicalNode
    | CreateNodeIdentifier
    | CreateNodeExternalId
    | UpdateCanonicalNodeTitle
    | MergeCanonicalNodes
)


@dataclass(frozen=True, slots=True)
class BoundEvidenceAnnotation:
    start: int
    length: int
    node_id: UUID


@dataclass(frozen=True, slots=True)
class BoundEvidence:
    source_id: UUID
    raw_content: str
    confidence: float
    strength_boost: float | None
    annotations: list[BoundEvidenceAnnotation] = field(default_factory=list)
    is_verbatim: bool = True


@dataclass(frozen=True, slots=True)
class UpsertCanonicalEdge:
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    confidence: float
    strength: float
    evidence: list[BoundEvidence]
    # update: the edge already has this relation type (absorbed_relation_type is
    # set when a broader proposed relation was folded into this edge).
    # specialize: rewrite edge_id from previous_relation_type to relation_type.
    operation: Literal["create", "update", "specialize"]
    is_symmetric: bool = False
    previous_relation_type: str | None = None
    absorbed_relation_type: str | None = None
    # Incoming details from this source; persistence merges them into the locked
    # edge row with merge_edge_details, like it does for confidence and strength.
    details: str | None = None
    kind: Literal["upsert_edge"] = "upsert_edge"


@dataclass(frozen=True, slots=True)
class ArchiveCanonicalEdge:
    edge_id: UUID
    reason: Literal[
        "superseded_non_temporal",
        "superseded_by_specific_relation",
    ] = "superseded_non_temporal"
    kind: Literal["archive_edge"] = "archive_edge"


EdgePlanAction: TypeAlias = UpsertCanonicalEdge | ArchiveCanonicalEdge


@dataclass(frozen=True, slots=True)
class ResolutionTransactionPlan:
    workspace_id: UUID
    source_id: UUID
    node_bindings: list[NodeBinding]
    node_actions: list[NodePlanAction]
    edge_actions: list[EdgePlanAction]
    repair_items: list[ResolveRepairItem]


def combine_probabilities(values: list[float]) -> float:
    """Combine independent supporting signals with diminishing returns."""

    return 1.0 - prod(1.0 - value for value in values)


def merge_edge_details(existing: str | None, incoming: str | None) -> str | None:
    """Accumulate edge details across mentions without repeating known facts.

    Details never affect edge identity, so a repeated relationship keeps one edge
    whose details grow as new sources qualify it.
    """

    existing = " ".join(existing.split()) if existing else None
    incoming = " ".join(incoming.split()) if incoming else None
    if not incoming:
        return existing or None
    if not existing:
        return incoming
    if _normalize(incoming) in _normalize(existing):
        return existing
    if _normalize(existing) in _normalize(incoming):
        return incoming
    return f"{existing}; {incoming}"


def evidence_confidence(evidence: list[ProposedEvidence]) -> float:
    return combine_probabilities([item.confidence for item in evidence])


def evidence_strength(evidence: list[ProposedEvidence]) -> float:
    return combine_probabilities(
        [
            (item.strength_boost or 0.0) * item.confidence
            for item in evidence
        ]
    )


async def resolve_proposed_subgraph(
    *,
    workspace_id: UUID,
    source_id: UUID,
    proposed_graph: ProposedGraph,
    store: CanonicalGraphResolverStore,
    policy: ResolutionPolicy = ResolutionPolicy(),
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ResolutionTransactionPlan:
    """Resolve proposed nodes and edges into a persistence-ready transaction plan."""

    node_bindings: list[NodeBinding] = []
    node_actions: list[NodePlanAction] = []
    edge_actions: list[EdgePlanAction] = []
    repair_items: list[ResolveRepairItem] = []
    bound_nodes: dict[str, CanonicalNode] = {}

    proposed_nodes = [_with_email_identity(node) for node in proposed_graph.nodes]
    candidates_by_node: dict[str, list[NodeCandidate]] = {}
    decisions_by_node: dict[
        str, tuple[Literal["resolve", "create", "ambiguous"], NodeCandidate | None]
    ] = {}
    for proposed_node in proposed_nodes:
        candidates = await store.find_node_candidates(
            workspace_id=workspace_id,
            proposed_node=proposed_node,
        )
        candidates_by_node[proposed_node.proposed_node_id] = candidates
        compatible = [
            candidate
            for candidate in candidates
            if candidate.node.node_type == proposed_node.node_type
        ]
        incompatible_external_matches = [
            candidate
            for candidate in candidates
            if candidate.match_kind == "external_id"
            and candidate.node.node_type != proposed_node.node_type
        ]
        decisions_by_node[proposed_node.proposed_node_id] = (
            ("ambiguous", None)
            if incompatible_external_matches
            else _choose_node_candidate(compatible, policy)
        )

    await _apply_graph_context(
        workspace_id=workspace_id,
        proposed_graph=proposed_graph,
        proposed_nodes=proposed_nodes,
        store=store,
        policy=policy,
        candidates_by_node=candidates_by_node,
        decisions_by_node=decisions_by_node,
    )

    for proposed_node in proposed_nodes:
        candidates = candidates_by_node[proposed_node.proposed_node_id]
        compatible = [
            candidate
            for candidate in candidates
            if candidate.node.node_type == proposed_node.node_type
        ]
        incompatible_external_matches = [
            candidate
            for candidate in candidates
            if candidate.match_kind == "external_id"
            and candidate.node.node_type != proposed_node.node_type
        ]
        decision, selected = decisions_by_node[proposed_node.proposed_node_id]

        if decision == "ambiguous":
            node_bindings.append(
                NodeBinding(proposed_node.proposed_node_id, None, "skipped")
            )
            repair_items.append(
                ResolveRepairItem(
                    item_type="ambiguous_identity",
                    source_id=source_id,
                    severity="high",
                    message=(
                        f"Could not safely resolve {proposed_node.primary_reference_name!r}; "
                        "multiple canonical nodes are plausible."
                    ),
                    proposed_node_id=proposed_node.proposed_node_id,
                    candidates=[
                        _repair_candidate(item)
                        for item in compatible + incompatible_external_matches
                    ],
                )
            )
            continue

        if selected is not None:
            canonical_node = selected.node
            node_bindings.append(
                NodeBinding(
                    proposed_node.proposed_node_id,
                    canonical_node.node_id,
                    "resolved",
                    selected.match_score,
                )
            )
            for duplicate in _mergeable_candidates(selected, compatible, policy):
                node_actions.append(
                    MergeCanonicalNodes(
                        from_node_id=duplicate.node.node_id,
                        into_node_id=canonical_node.node_id,
                        reason="high-confidence identity match",
                    )
                )
                node_actions.append(
                    CreateNodeIdentifier(canonical_node.node_id, duplicate.node.title)
                )
                if _is_email(duplicate.node.title):
                    node_actions.append(
                        CreateNodeExternalId(
                            canonical_node.node_id,
                            "email",
                            _normalized_email(duplicate.node.title),
                        )
                    )
        else:
            canonical_node = CanonicalNode(
                node_id=uuid_factory(),
                node_type=proposed_node.node_type,
                title=proposed_node.primary_reference_name,
            )
            duplicate_candidates = [
                _repair_candidate(item)
                for item in compatible
                if item.match_score >= policy.review_match_threshold
            ]
            node_bindings.append(
                NodeBinding(
                    proposed_node.proposed_node_id,
                    canonical_node.node_id,
                    "created",
                )
            )
            node_actions.append(
                CreateCanonicalNode(
                    node_id=canonical_node.node_id,
                    node_type=proposed_node.node_type,
                    title=proposed_node.primary_reference_name,
                    summary=proposed_node.summary,
                    type_specific=proposed_node.type_specific,
                    idempotency_key=(
                        f"{source_id}:proposed-node:{proposed_node.proposed_node_id}"
                    ),
                    duplicate_candidates=duplicate_candidates,
                )
            )
            if duplicate_candidates:
                repair_items.append(
                    ResolveRepairItem(
                        item_type="possible_duplicate",
                        source_id=source_id,
                        severity="medium",
                        message=(
                            f"Created {proposed_node.primary_reference_name!r}, but "
                            "one or more existing nodes may represent the same identity."
                        ),
                        proposed_node_id=proposed_node.proposed_node_id,
                        candidates=duplicate_candidates,
                    )
                )

        preferred_title = _preferred_title(
            canonical_node.title,
            proposed_node.primary_reference_name,
            proposed_node.node_type,
        )
        if selected is not None and preferred_title != canonical_node.title:
            promoted_external_ids = list(canonical_node.external_ids)
            node_actions.append(
                CreateNodeIdentifier(canonical_node.node_id, canonical_node.title)
            )
            if _is_email(canonical_node.title):
                promoted_email = ProposedExternalId(
                    "email", _normalized_email(canonical_node.title)
                )
                node_actions.append(
                    CreateNodeExternalId(
                        canonical_node.node_id,
                        promoted_email.provider,
                        promoted_email.external_id,
                    )
                )
                promoted_external_ids.append(promoted_email)
            node_actions.append(
                UpdateCanonicalNodeTitle(canonical_node.node_id, preferred_title)
            )
            canonical_node = CanonicalNode(
                node_id=canonical_node.node_id,
                node_type=canonical_node.node_type,
                title=preferred_title,
                identifiers=[*canonical_node.identifiers, selected.node.title],
                external_ids=promoted_external_ids,
            )

        bound_nodes[proposed_node.proposed_node_id] = canonical_node
        node_actions.extend(
            _identifier_actions(proposed_node, canonical_node, policy)
        )
        node_actions.extend(_external_id_actions(proposed_node, canonical_node))

    relation_catalog = (
        {
            item.name: item
            for item in await store.list_relation_types(workspace_id=workspace_id)
        }
        if proposed_graph.edges
        else {}
    )
    # Edges as this plan leaves them, so later proposed edges for the same pair
    # see earlier creates and specializations from the same source.
    planned_edges: dict[UUID, CanonicalEdge] = {}
    archived_edge_ids: set[UUID] = set()

    for proposed_edge in proposed_graph.edges:
        missing_ids = _missing_bound_node_ids(proposed_edge, bound_nodes)
        if missing_ids:
            repair_items.append(
                ResolveRepairItem(
                    item_type="unresolved_edge_endpoint",
                    source_id=source_id,
                    severity="high",
                    message=(
                        f"Skipped edge {proposed_edge.proposed_edge_id!r} because "
                        f"these proposed nodes were unresolved: {', '.join(missing_ids)}."
                    ),
                    proposed_edge_id=proposed_edge.proposed_edge_id,
                )
            )
            continue

        from_node = bound_nodes[proposed_edge.from_proposed_node_id]
        to_node = bound_nodes[proposed_edge.to_proposed_node_id]
        relation_resolution = await store.resolve_relation_type(
            workspace_id=workspace_id,
            relation_type=proposed_edge.relation_type,
        )
        if relation_resolution is None:
            repair_items.append(
                ResolveRepairItem(
                    item_type="unsupported_relation_type",
                    source_id=source_id,
                    severity="medium",
                    message=(
                        f"Stored edge {proposed_edge.proposed_edge_id!r} as "
                        f"'related_to'; "
                        f"relation {proposed_edge.relation_type!r} does not map "
                        "to the fixed relation catalog."
                    ),
                    proposed_edge_id=proposed_edge.proposed_edge_id,
                    details=_edge_repair_details(
                        proposed_edge,
                        from_node=from_node,
                        to_node=to_node,
                    ),
                )
            )
            fallback = await store.resolve_relation_type(
                workspace_id=workspace_id,
                relation_type="related_to",
            )
            if fallback is None:
                raise RuntimeError(
                    "The active relation catalog must contain 'related_to'."
                )
            relation, reverse_endpoints = fallback
        else:
            relation, reverse_endpoints = relation_resolution
        if reverse_endpoints:
            from_node, to_node = to_node, from_node
        elif relation.is_symmetric and str(from_node.node_id) > str(to_node.node_id):
            from_node, to_node = to_node, from_node

        if (
            relation.allowed_from_types
            and from_node.node_type not in relation.allowed_from_types
        ) or (
            relation.allowed_to_types
            and to_node.node_type not in relation.allowed_to_types
        ):
            repair_items.append(
                ResolveRepairItem(
                    item_type="invalid_relation_endpoints",
                    source_id=source_id,
                    severity="medium",
                    message=(
                        f"Stored edge {proposed_edge.proposed_edge_id!r} as "
                        f"'related_to'; "
                        f"{relation.name!r} does not support endpoint types "
                        f"{from_node.node_type!r} -> {to_node.node_type!r}."
                    ),
                    proposed_edge_id=proposed_edge.proposed_edge_id,
                    details=_edge_repair_details(
                        proposed_edge,
                        from_node=from_node,
                        to_node=to_node,
                        canonical_relation_type=relation.name,
                    ),
                )
            )
            fallback = await store.resolve_relation_type(
                workspace_id=workspace_id,
                relation_type="related_to",
            )
            if fallback is None:
                raise RuntimeError(
                    "The active relation catalog must contain 'related_to'."
                )
            relation, _ = fallback
            if str(from_node.node_id) > str(to_node.node_id):
                from_node, to_node = to_node, from_node

        incoming_confidence = evidence_confidence(proposed_edge.evidence)
        incoming_strength = evidence_strength(proposed_edge.evidence)
        bound_evidence = _bind_evidence(
            source_id,
            proposed_edge.evidence,
            bound_nodes,
        )

        # Compare against every active edge on this node pair. More specific
        # relations always win: a broader incoming relation is absorbed into a
        # narrower edge, and a narrower one specializes the broader edge.
        # Unrelated relation types, or asymmetric edges pointing the other
        # way, are independent edges.
        incoming_ancestors = relation_ancestors(relation.name, relation_catalog)
        exact_edge: CanonicalEdge | None = None
        broader_edges: list[CanonicalEdge] = []
        narrower_edges: list[CanonicalEdge] = []
        for edge in await _active_pair_edges(
            store,
            workspace_id=workspace_id,
            node_id=from_node.node_id,
            other_node_id=to_node.node_id,
            planned_edges=planned_edges,
            archived_edge_ids=archived_edge_ids,
        ):
            if not _directions_compatible(
                edge,
                from_node_id=from_node.node_id,
                to_node_id=to_node.node_id,
                relation=relation,
                relation_catalog=relation_catalog,
                policy=policy,
            ):
                continue
            if edge.relation_type == relation.name:
                exact_edge = edge
            elif (
                edge.relation_type == policy.unknown_relation_type
                or edge.relation_type in incoming_ancestors
            ):
                broader_edges.append(edge)
            elif relation.name in relation_ancestors(
                edge.relation_type, relation_catalog
            ):
                narrower_edges.append(edge)

        def specificity(edge: CanonicalEdge) -> tuple[int, float, str]:
            depth = (
                -1
                if edge.relation_type == policy.unknown_relation_type
                else len(relation_ancestors(edge.relation_type, relation_catalog))
            )
            return depth, edge.confidence, str(edge.edge_id)

        if narrower_edges:
            target = max(narrower_edges, key=specificity)
            action = UpsertCanonicalEdge(
                edge_id=target.edge_id,
                from_node_id=target.from_node_id,
                to_node_id=target.to_node_id,
                relation_type=target.relation_type,
                confidence=_combine_with_existing(
                    target.confidence, incoming_confidence
                ),
                strength=_combine_with_existing(target.strength, incoming_strength),
                evidence=bound_evidence,
                operation="update",
                is_symmetric=relation_catalog[target.relation_type].is_symmetric,
                absorbed_relation_type=relation.name,
                details=merge_edge_details(None, proposed_edge.details),
            )
        else:
            if exact_edge is None and relation.is_exclusive:
                conflicts = [
                    edge
                    for edge in await store.find_active_outgoing_edges(
                        workspace_id=workspace_id,
                        from_node_id=from_node.node_id,
                        relation_type=relation.name,
                    )
                    if edge.to_node_id != to_node.node_id
                ]
                if (
                    conflicts
                    and incoming_confidence < policy.exclusive_replacement_threshold
                ):
                    repair_items.append(
                        ResolveRepairItem(
                            item_type="conflicting_exclusive_fact",
                            source_id=source_id,
                            severity="high",
                            message=(
                                f"Skipped exclusive relation {relation.name!r}; "
                                "the new evidence is not strong enough to replace the active edge."
                            ),
                            proposed_edge_id=proposed_edge.proposed_edge_id,
                        )
                    )
                    continue
                edge_actions.extend(
                    ArchiveCanonicalEdge(edge.edge_id) for edge in conflicts
                )

            # Reuse the exact edge, else the most specific broader edge. Any
            # remaining broader edges on the pair are now redundant.
            reusable = [
                *([exact_edge] if exact_edge is not None else []),
                *sorted(broader_edges, key=specificity, reverse=True),
            ]
            prior_edge = reusable[0] if reusable else None
            for edge in reusable[1:]:
                if edge.edge_id in planned_edges:
                    continue
                edge_actions.append(
                    ArchiveCanonicalEdge(
                        edge.edge_id,
                        reason="superseded_by_specific_relation",
                    )
                )
                archived_edge_ids.add(edge.edge_id)

            action = UpsertCanonicalEdge(
                edge_id=prior_edge.edge_id if prior_edge is not None else uuid_factory(),
                from_node_id=from_node.node_id,
                to_node_id=to_node.node_id,
                relation_type=relation.name,
                confidence=_combine_with_existing(
                    prior_edge.confidence if prior_edge is not None else None,
                    incoming_confidence,
                ),
                strength=_combine_with_existing(
                    prior_edge.strength if prior_edge is not None else None,
                    incoming_strength,
                ),
                evidence=bound_evidence,
                operation=(
                    "update"
                    if exact_edge is not None
                    else "specialize"
                    if prior_edge is not None
                    else "create"
                ),
                is_symmetric=relation.is_symmetric,
                previous_relation_type=(
                    prior_edge.relation_type
                    if prior_edge is not None and exact_edge is None
                    else None
                ),
                details=merge_edge_details(None, proposed_edge.details),
            )

        edge_actions.append(action)
        planned_edges[action.edge_id] = CanonicalEdge(
            edge_id=action.edge_id,
            from_node_id=action.from_node_id,
            to_node_id=action.to_node_id,
            relation_type=action.relation_type,
            confidence=action.confidence,
            strength=action.strength,
        )

    return ResolutionTransactionPlan(
        workspace_id=workspace_id,
        source_id=source_id,
        node_bindings=node_bindings,
        node_actions=node_actions,
        edge_actions=edge_actions,
        repair_items=repair_items,
    )


async def _active_pair_edges(
    store: CanonicalGraphResolverStore,
    *,
    workspace_id: UUID,
    node_id: UUID,
    other_node_id: UUID,
    planned_edges: dict[UUID, CanonicalEdge],
    archived_edge_ids: set[UUID],
) -> list[CanonicalEdge]:
    """Return the pair's active edges with this plan's earlier actions applied."""

    pair = {node_id, other_node_id}
    edges = {
        edge.edge_id: edge
        for edge in await store.find_edges_between(
            workspace_id=workspace_id,
            node_id=node_id,
            other_node_id=other_node_id,
        )
        if edge.edge_id not in archived_edge_ids
    }
    edges.update(
        (edge_id, edge)
        for edge_id, edge in planned_edges.items()
        if {edge.from_node_id, edge.to_node_id} == pair
    )
    return sorted(edges.values(), key=lambda edge: str(edge.edge_id))


def _directions_compatible(
    edge: CanonicalEdge,
    *,
    from_node_id: UUID,
    to_node_id: UUID,
    relation: RelationType,
    relation_catalog: dict[str, RelationType],
    policy: ResolutionPolicy,
) -> bool:
    """Direction only matters when both relations are asymmetric."""

    if relation.is_symmetric or edge.relation_type == policy.unknown_relation_type:
        return True
    existing = relation_catalog.get(edge.relation_type)
    if existing is None or existing.is_symmetric:
        return True
    return edge.from_node_id == from_node_id and edge.to_node_id == to_node_id


async def _apply_graph_context(
    *,
    workspace_id: UUID,
    proposed_graph: ProposedGraph,
    proposed_nodes: list[ProposedNode],
    store: CanonicalGraphResolverStore,
    policy: ResolutionPolicy,
    candidates_by_node: dict[str, list[NodeCandidate]],
    decisions_by_node: dict[
        str, tuple[Literal["resolve", "create", "ambiguous"], NodeCandidate | None]
    ],
) -> None:
    find_neighbors = getattr(store, "find_neighbor_node_ids", None)
    if find_neighbors is None:
        return
    directly_resolved = {
        proposed_node_id: selected.node.node_id
        for proposed_node_id, (decision, selected) in decisions_by_node.items()
        if decision == "resolve" and selected is not None
    }
    adjacent: dict[str, set[str]] = {
        node.proposed_node_id: set() for node in proposed_nodes
    }
    for edge in proposed_graph.edges:
        adjacent.setdefault(edge.from_proposed_node_id, set()).add(
            edge.to_proposed_node_id
        )
        adjacent.setdefault(edge.to_proposed_node_id, set()).add(
            edge.from_proposed_node_id
        )

    for proposed_node in proposed_nodes:
        node_id = proposed_node.proposed_node_id
        decision, _ = decisions_by_node[node_id]
        if decision != "create":
            continue
        resolved_neighbor_ids = {
            directly_resolved[neighbor]
            for neighbor in adjacent.get(node_id, set())
            if neighbor in directly_resolved
        }
        if not resolved_neighbor_ids:
            continue
        rescored: list[NodeCandidate] = []
        for candidate in candidates_by_node[node_id]:
            if candidate.node.node_type != proposed_node.node_type:
                rescored.append(candidate)
                continue
            canonical_neighbors = await find_neighbors(
                workspace_id=workspace_id,
                node_id=candidate.node.node_id,
            )
            shared_count = len(resolved_neighbor_ids & canonical_neighbors)
            contextual_score = (
                policy.two_shared_neighbors_score
                if shared_count >= 2
                else policy.one_shared_neighbor_score
                if shared_count == 1
                else candidate.match_score
            )
            rescored.append(
                NodeCandidate(
                    node=candidate.node,
                    match_kind=candidate.match_kind,
                    match_score=max(candidate.match_score, contextual_score),
                    reasons=[
                        *candidate.reasons,
                        *(
                            [f"shares {shared_count} resolved graph neighbor(s)"]
                            if shared_count
                            else []
                        ),
                    ],
                )
            )
        rescored.sort(key=lambda item: item.match_score, reverse=True)
        candidates_by_node[node_id] = rescored
        compatible = [
            candidate
            for candidate in rescored
            if candidate.node.node_type == proposed_node.node_type
        ]
        decisions_by_node[node_id] = _choose_node_candidate(compatible, policy)


def _choose_node_candidate(
    candidates: list[NodeCandidate],
    policy: ResolutionPolicy,
) -> tuple[Literal["resolve", "create", "ambiguous"], NodeCandidate | None]:
    ordered = sorted(candidates, key=lambda item: item.match_score, reverse=True)
    exact = [item for item in ordered if item.match_kind != "fuzzy"]
    exact_node_ids = {item.node.node_id for item in exact}
    if len(exact_node_ids) > 1:
        return "ambiguous", None
    if exact:
        return "resolve", exact[0]
    if not ordered or ordered[0].match_score < policy.fuzzy_resolve_threshold:
        return "create", None
    if (
        len(ordered) > 1
        and ordered[0].match_score - ordered[1].match_score
        < policy.ambiguous_score_gap
    ):
        return "ambiguous", None
    return "resolve", ordered[0]


_EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)
_LEET_TRANSLATION = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"}
)


def _is_email(value: str) -> bool:
    return _EMAIL_PATTERN.fullmatch(value.strip()) is not None


def _normalized_email(value: str) -> str:
    return value.strip().casefold()


def _email_local_identity(value: str) -> str | None:
    if not _is_email(value):
        return None
    local_part = value.split("@", 1)[0].translate(_LEET_TRANSLATION)
    return _normalize(re.sub(r"[^a-z0-9]+", " ", local_part))


def _with_email_identity(node: ProposedNode) -> ProposedNode:
    """Return a node with any email-shaped names represented as external IDs."""

    external_ids = list(node.external_ids)
    known = {
        (item.provider.casefold(), item.external_id.casefold())
        for item in external_ids
    }
    for name in [node.primary_reference_name, *node.names]:
        if not _is_email(name):
            continue
        email = _normalized_email(name)
        if ("email", email) not in known:
            external_ids.append(ProposedExternalId("email", email))
            known.add(("email", email))

    primary = node.primary_reference_name
    readable_names = [name for name in node.names if not _is_email(name)]
    if _is_email(primary) and readable_names:
        primary = max(
            readable_names,
            key=lambda name: (_title_quality(name, node.node_type), len(name)),
        )

    return ProposedNode(
        proposed_node_id=node.proposed_node_id,
        node_type=node.node_type,
        names=list(dict.fromkeys([*node.names, primary])),
        primary_reference_name=primary,
        summary=node.summary,
        type_specific=node.type_specific,
        external_ids=external_ids,
    )


def _email_identity_match(
    proposed_names: set[str],
    canonical_node: CanonicalNode,
) -> str | None:
    if canonical_node.node_type != "person":
        return None
    email_values = [
        value
        for value in [canonical_node.title, *canonical_node.identifiers]
        if _is_email(value)
    ]
    email_values.extend(
        item.external_id
        for item in canonical_node.external_ids
        if item.provider.casefold() == "email" and _is_email(item.external_id)
    )
    for email in email_values:
        local_identity = _email_local_identity(email)
        if local_identity is None:
            continue
        for proposed_name in proposed_names:
            normalized_name = _normalize(
                re.sub(r"[^a-z0-9]+", " ", proposed_name.translate(_LEET_TRANSLATION))
            )
            if normalized_name == local_identity:
                return f"email local-part {email!r} matches proposed name {proposed_name!r}"
    return None


def _title_quality(value: str, node_type: NodeType | str) -> tuple[int, int, int]:
    stripped = value.strip()
    if _is_email(stripped):
        return (0, 0, len(stripped))
    words = stripped.split()
    compact = re.sub(r"[^A-Za-z0-9]", "", stripped)
    acronym = len(words) == 1 and len(compact) <= 5
    readable_case = int(any(character.isupper() for character in stripped))
    if node_type in {"person", "location"} and len(words) >= 2:
        return (4, readable_case, len(stripped))
    if acronym:
        return (1, readable_case, len(stripped))
    return (3, readable_case, len(stripped))


def _preferred_title(
    current: str,
    proposed: str,
    node_type: NodeType | str,
) -> str:
    if _normalize(current) == _normalize(proposed):
        return current
    if _title_quality(proposed, node_type) > _title_quality(current, node_type):
        return proposed
    return current


def _mergeable_candidates(
    selected: NodeCandidate,
    candidates: list[NodeCandidate],
    policy: ResolutionPolicy,
) -> list[NodeCandidate]:
    return [
        candidate
        for candidate in candidates
        if candidate.node.node_id != selected.node.node_id
        and candidate.match_score >= policy.merge_match_threshold
        and any("email local-part" in reason for reason in candidate.reasons)
    ]


def _identifier_actions(
    proposed_node: ProposedNode,
    canonical_node: CanonicalNode,
    policy: ResolutionPolicy,
) -> list[CreateNodeIdentifier]:
    known = {_normalize(value) for value in canonical_node.identifiers}
    known.add(_normalize(canonical_node.title))
    actions: list[CreateNodeIdentifier] = []
    for name in proposed_node.names:
        normalized_name = _normalize(name)
        if normalized_name in known:
            continue
        similarity = SequenceMatcher(
            None,
            normalized_name,
            _normalize(canonical_node.title),
        ).ratio()
        if similarity >= policy.identifier_similarity_threshold:
            continue
        actions.append(CreateNodeIdentifier(canonical_node.node_id, name))
        known.add(normalized_name)
    return actions


def _external_id_actions(
    proposed_node: ProposedNode,
    canonical_node: CanonicalNode,
) -> list[CreateNodeExternalId]:
    known = {
        (item.provider.casefold(), item.external_id)
        for item in canonical_node.external_ids
    }
    actions: list[CreateNodeExternalId] = []
    for external_id in proposed_node.external_ids:
        key = (external_id.provider.casefold(), external_id.external_id)
        if key in known:
            continue
        actions.append(
            CreateNodeExternalId(
                canonical_node.node_id,
                external_id.provider,
                external_id.external_id,
            )
        )
        known.add(key)
    return actions


def _missing_bound_node_ids(
    edge: ProposedEdge,
    bound_nodes: dict[str, CanonicalNode],
) -> list[str]:
    referenced = [edge.from_proposed_node_id, edge.to_proposed_node_id]
    referenced.extend(
        annotation.local_node_id
        for evidence in edge.evidence
        for annotation in evidence.annotations
    )
    return list(dict.fromkeys(item for item in referenced if item not in bound_nodes))


def _edge_repair_details(
    edge: ProposedEdge,
    *,
    from_node: CanonicalNode,
    to_node: CanonicalNode,
    canonical_relation_type: str | None = None,
) -> JsonObject:
    return {
        "from_node_id": str(from_node.node_id),
        "from_name": from_node.title,
        "from_node_type": str(from_node.node_type),
        "to_node_id": str(to_node.node_id),
        "to_name": to_node.title,
        "to_node_type": str(to_node.node_type),
        "proposed_relation_type": edge.relation_type,
        "canonical_relation_type": canonical_relation_type,
        "details": edge.details,
        "evidence": [item.raw_content for item in edge.evidence],
    }


def _bind_evidence(
    source_id: UUID,
    evidence: list[ProposedEvidence],
    bound_nodes: dict[str, CanonicalNode],
) -> list[BoundEvidence]:
    return [
        BoundEvidence(
            source_id=source_id,
            raw_content=item.raw_content,
            confidence=item.confidence,
            strength_boost=item.strength_boost,
            is_verbatim=item.is_verbatim,
            annotations=[
                BoundEvidenceAnnotation(
                    start=annotation.start,
                    length=annotation.length,
                    node_id=bound_nodes[annotation.local_node_id].node_id,
                )
                for annotation in item.annotations
            ],
        )
        for item in evidence
    ]


def _combine_with_existing(existing: float | None, incoming: float) -> float:
    if existing is None:
        return incoming
    return combine_probabilities([existing, incoming])


def _repair_candidate(candidate: NodeCandidate) -> ResolveRepairCandidate:
    return ResolveRepairCandidate(
        node_id=candidate.node.node_id,
        title=candidate.node.title,
        node_type=str(candidate.node.node_type),
        match_score=candidate.match_score,
        reasons=candidate.reasons,
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
