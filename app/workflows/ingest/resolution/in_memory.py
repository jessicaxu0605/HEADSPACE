"""In-memory adapter for canonical graph resolution."""

from difflib import SequenceMatcher
from uuid import UUID

from app.workflows.ingest.enrichment.fields import type_specific_identity_key
from app.workflows.ingest.models import ProposedNode
from app.workflows.ingest.relation_types import canonicalize_relation_type
from app.workflows.ingest.resolution.service import (
    CanonicalEdge,
    CanonicalNode,
    NodeCandidate,
    RelationType,
    _email_identity_match,
    _normalize,
)


class SimpleCanonicalGraphResolverStore:
    """Small lexical resolver over an in-memory canonical graph snapshot.

    This is the baseline implementation for the prototype. The PostgreSQL adapter
    loads the same records and delegates ingestion candidate scoring here.
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

        proposed_key = type_specific_identity_key(
            proposed_node.node_type, proposed_node.type_specific
        )
        canonical_key = type_specific_identity_key(
            canonical_node.node_type, canonical_node.type_specific
        )
        if proposed_key is not None and canonical_key is not None:
            if proposed_key != canonical_key:
                # Different dates or places, however similar their names.
                return None
            return NodeCandidate(
                canonical_node,
                "type_specific",
                0.99,
                [f"matching {canonical_node.node_type} identity {canonical_key!r}"],
            )

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



