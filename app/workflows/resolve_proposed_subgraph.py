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
from typing import Literal, Protocol, TypeAlias
from uuid import UUID, uuid4

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
    identifier_similarity_threshold: float = 0.65
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


@dataclass(frozen=True, slots=True)
class RelationType:
    name: str
    is_exclusive: bool = False
    can_change_over_time: bool = False


class CanonicalGraphResolverStore(Protocol):
    """Read surface needed by resolution; implementations may use SQL/search/LLMs."""

    async def find_node_candidates(
        self,
        *,
        workspace_id: UUID,
        proposed_node: ProposedNode,
    ) -> list[NodeCandidate]: ...

    async def find_edge(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        relation_type: str,
    ) -> CanonicalEdge | None: ...

    async def get_relation_type(
        self,
        *,
        workspace_id: UUID,
        relation_type: str,
    ) -> RelationType | None: ...

    async def find_active_outgoing_edges(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        relation_type: str,
    ) -> list[CanonicalEdge]: ...


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

    async def find_edge(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        relation_type: str,
    ) -> CanonicalEdge | None:
        del workspace_id
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

    async def get_relation_type(
        self,
        *,
        workspace_id: UUID,
        relation_type: str,
    ) -> RelationType | None:
        del workspace_id
        return self.relation_types.get(relation_type)

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
        "unresolved_edge_endpoint",
        "conflicting_exclusive_fact",
    ]
    source_id: UUID
    severity: Literal["low", "medium", "high"]
    message: str
    proposed_node_id: str | None = None
    proposed_edge_id: str | None = None
    candidates: list[ResolveRepairCandidate] = field(default_factory=list)


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


NodePlanAction: TypeAlias = (
    CreateCanonicalNode | CreateNodeIdentifier | CreateNodeExternalId
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


@dataclass(frozen=True, slots=True)
class UpsertCanonicalEdge:
    edge_id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    confidence: float
    strength: float
    evidence: list[BoundEvidence]
    operation: Literal["create", "update", "upgrade_unknown"]
    previous_relation_type: str | None = None
    kind: Literal["upsert_edge"] = "upsert_edge"


@dataclass(frozen=True, slots=True)
class ArchiveCanonicalEdge:
    edge_id: UUID
    reason: Literal["superseded_non_temporal"] = "superseded_non_temporal"
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

    for proposed_node in proposed_graph.nodes:
        candidates = await store.find_node_candidates(
            workspace_id=workspace_id,
            proposed_node=proposed_node,
        )
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
        decision, selected = (
            ("ambiguous", None)
            if incompatible_external_matches
            else _choose_node_candidate(compatible, policy)
        )

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
        else:
            canonical_node = CanonicalNode(
                node_id=uuid_factory(),
                node_type=proposed_node.node_type,
                title=proposed_node.primary_reference_name,
            )
            medium_candidates = [
                _repair_candidate(item)
                for item in compatible
                if item.match_score >= policy.medium_match_threshold
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
                    duplicate_candidates=medium_candidates,
                )
            )

        bound_nodes[proposed_node.proposed_node_id] = canonical_node
        node_actions.extend(
            _identifier_actions(proposed_node, canonical_node, policy)
        )
        node_actions.extend(_external_id_actions(proposed_node, canonical_node))

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
        incoming_confidence = evidence_confidence(proposed_edge.evidence)
        incoming_strength = evidence_strength(proposed_edge.evidence)

        existing_edge = await store.find_edge(
            workspace_id=workspace_id,
            from_node_id=from_node.node_id,
            to_node_id=to_node.node_id,
            relation_type=proposed_edge.relation_type,
        )
        unknown_edge = None
        if existing_edge is None and proposed_edge.relation_type != policy.unknown_relation_type:
            unknown_edge = await store.find_edge(
                workspace_id=workspace_id,
                from_node_id=from_node.node_id,
                to_node_id=to_node.node_id,
                relation_type=policy.unknown_relation_type,
            )

        relation = await store.get_relation_type(
            workspace_id=workspace_id,
            relation_type=proposed_edge.relation_type,
        )
        if existing_edge is None and relation is not None and relation.is_exclusive:
            conflicts = [
                edge
                for edge in await store.find_active_outgoing_edges(
                    workspace_id=workspace_id,
                    from_node_id=from_node.node_id,
                    relation_type=proposed_edge.relation_type,
                )
                if edge.to_node_id != to_node.node_id
            ]
            if conflicts and incoming_confidence < policy.exclusive_replacement_threshold:
                repair_items.append(
                    ResolveRepairItem(
                        item_type="conflicting_exclusive_fact",
                        source_id=source_id,
                        severity="high",
                        message=(
                            f"Skipped exclusive relation {proposed_edge.relation_type!r}; "
                            "the new evidence is not strong enough to replace the active edge."
                        ),
                        proposed_edge_id=proposed_edge.proposed_edge_id,
                    )
                )
                continue
            edge_actions.extend(ArchiveCanonicalEdge(edge.edge_id) for edge in conflicts)

        bound_evidence = _bind_evidence(
            source_id,
            proposed_edge.evidence,
            bound_nodes,
        )
        prior_edge = existing_edge or unknown_edge
        edge_actions.append(
            UpsertCanonicalEdge(
                edge_id=prior_edge.edge_id if prior_edge is not None else uuid_factory(),
                from_node_id=from_node.node_id,
                to_node_id=to_node.node_id,
                relation_type=proposed_edge.relation_type,
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
                    if existing_edge is not None
                    else "upgrade_unknown"
                    if unknown_edge is not None
                    else "create"
                ),
                previous_relation_type=(
                    unknown_edge.relation_type if unknown_edge is not None else None
                ),
            )
        )

    return ResolutionTransactionPlan(
        workspace_id=workspace_id,
        source_id=source_id,
        node_bindings=node_bindings,
        node_actions=node_actions,
        edge_actions=edge_actions,
        repair_items=repair_items,
    )


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
