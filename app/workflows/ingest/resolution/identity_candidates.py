"""Identity-projection candidates for resolve-or-create.

Design V1 lists "identity embedding similarity" among the fuzzy resolution
signals: the proposed node is described the way canonical identity projections
are, embedded, and compared with stored identity vectors.

Measured on nomic-embed-text, short identity texts do not separate same from
different entities: "Sarah Chen" vs "Sarah Kim" scores ~0.86, the same as
"Sarah Chen" vs "Sarah C", and "UW" vs "UWaterloo" scores below "UW" vs the
city of Waterloo. So these candidates only widen the candidate set. Their score
is capped below ``ResolutionPolicy.fuzzy_resolve_threshold``; on their own they
yield review items, and they resolve only when graph context (shared resolved
neighbors) raises the score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from app.graph.identity_description import render_identity_description
from app.workflows.ingest.resolution.service import (
    FUZZY_MATCH_KINDS,
    CanonicalNode,
    NodeCandidate,
)
from app.workflows.ingest.enrichment.fields import type_specific_identity_key
from app.workflows.ingest.models import ProposedNode


IDENTITY_EMBEDDING_THRESHOLD = 0.85
IDENTITY_EMBEDDING_MAX_SCORE = 0.90
IDENTITY_EMBEDDING_LIMIT = 5


def proposed_identity_text(node: ProposedNode) -> str:
    """Describe a proposed node in the same format as stored identity projections."""

    instagram_aliases = [
        f"@{item.external_id.removeprefix('@')}"
        for item in node.external_ids
        if item.provider.casefold() == "instagram"
    ]
    return render_identity_description(
        name=node.primary_reference_name,
        node_type=node.node_type,
        aliases=[*instagram_aliases, *sorted(node.names, key=str.casefold)],
        external_providers=[item.provider for item in node.external_ids],
        type_specific=node.type_specific,
    )


def needs_identity_embedding(lexical: Sequence[NodeCandidate]) -> bool:
    """Skip the embedding call when lexical matching already found an exact match."""

    return all(candidate.match_kind in FUZZY_MATCH_KINDS for candidate in lexical)


def identity_embedding_candidate(
    proposed_node: ProposedNode,
    canonical_node: CanonicalNode,
    similarity: float,
) -> NodeCandidate | None:
    """Apply resolution's hard identity rules to one vector match."""

    if similarity < IDENTITY_EMBEDDING_THRESHOLD:
        return None
    if canonical_node.node_type != proposed_node.node_type:
        return None
    proposed_key = type_specific_identity_key(
        proposed_node.node_type, proposed_node.type_specific
    )
    canonical_key = type_specific_identity_key(
        canonical_node.node_type, canonical_node.type_specific
    )
    if proposed_key is not None and canonical_key is not None and proposed_key != canonical_key:
        # Different dates or places, however similar their descriptions.
        return None
    return NodeCandidate(
        canonical_node,
        "identity_embedding",
        min(similarity, IDENTITY_EMBEDDING_MAX_SCORE),
        [f"identity description is semantically similar ({similarity:.2f})"],
    )


def merge_identity_candidates(
    lexical: Sequence[NodeCandidate],
    embedding: Sequence[NodeCandidate],
) -> list[NodeCandidate]:
    """Add embedding candidates; a node found both ways keeps its stronger match."""

    merged = {candidate.node.node_id: candidate for candidate in lexical}
    for candidate in embedding:
        current = merged.get(candidate.node.node_id)
        if current is None:
            merged[candidate.node.node_id] = candidate
            continue
        stronger, weaker = (
            (candidate, current)
            if candidate.match_score > current.match_score
            else (current, candidate)
        )
        merged[candidate.node.node_id] = replace(
            stronger, reasons=list(dict.fromkeys([*stronger.reasons, *weaker.reasons]))
        )
    return sorted(
        merged.values(),
        key=lambda candidate: (
            -candidate.match_score,
            candidate.node.title.casefold(),
            str(candidate.node.node_id),
        ),
    )
