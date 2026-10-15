"""Client node search: find nodes related to a user's search keywords.

This is separate from ingestion resolution (``find_node_candidates``), which
asks "is this proposed node the same thing as an existing one?" and must be
conservative. Client search asks "which nodes is this query about?" and favors
recall. They share retrieval primitives, not an interface or result type.

Two mechanisms produce matches:

- lexical: exact external IDs, identifiers, and titles, then trigram-fuzzy
  names and identifiers;
- semantic: cosine similarity between the embedded query and each node's
  ``identity`` ("what is this?") and ``profile`` ("what is it associated
  with?") search projections.

Ranking:

1. Exact lexical matches (external ID, identifier, title) come first, by score.
2. Everything else is ordered by reciprocal rank fusion of two rankings: fuzzy
   lexical and vector. A node's fused score is the sum of ``1 / (k + rank)``
   over the rankings it appears in, so a node found by both rises. Ranks are
   used instead of scores because trigram similarity and cosine similarity are
   on different scales. Identity and profile are one vector ranking: they embed
   overlapping text about the same node, so they are not independent votes.

``score`` on each result stays the best raw score from its strongest
mechanism, for display; the fused score only orders results.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, TypeAlias
from uuid import UUID

from app.workflows.ingest.types import NodeType, ProposedExternalId


NodeSearchMatchKind: TypeAlias = Literal[
    "external_id",
    "identifier",
    "normalized_title",
    "fuzzy",
    "identity",
    "profile",
]

# nomic-embed-text puts unrelated short texts at roughly 0.35-0.5 cosine
# similarity; below this a semantic hit is noise.
DEFAULT_SEMANTIC_THRESHOLD = 0.55
DEFAULT_FUZZY_THRESHOLD = 0.50

# Standard RRF constant; larger values flatten the advantage of top ranks.
RRF_K = 60
# Each mechanism contributes at least this many candidates to fusion, so a node
# ranked just outside one list's top ``limit`` can still be lifted by the other.
MIN_FUSION_DEPTH = 20
EXACT_MATCH_KINDS: frozenset[str] = frozenset(
    {"external_id", "identifier", "normalized_title"}
)


@dataclass(frozen=True, slots=True)
class NodeSearchResult:
    node_id: UUID
    node_type: str
    title: str
    match_kind: NodeSearchMatchKind
    score: float
    identifiers: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class NodeSearchStore(Protocol):
    async def search_nodes(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        aliases: list[str],
        external_ids: list[ProposedExternalId],
        limit: int,
    ) -> list[NodeSearchResult]: ...


class QueryEmbedder(Protocol):
    model: str

    async def embed_query(self, text: str) -> list[float]: ...


def merge_search_results(
    lexical: Sequence[NodeSearchResult],
    semantic: Sequence[NodeSearchResult],
    *,
    limit: int,
    k: int = RRF_K,
) -> list[NodeSearchResult]:
    """Pin exact matches, then fuse fuzzy and vector rankings with RRF.

    Each input is ranked by its own ``score``. A node may appear more than once
    in ``semantic`` (identity and profile); its best appearance is its rank.
    """

    exact = _best_per_node(r for r in lexical if r.match_kind in EXACT_MATCH_KINDS)
    fuzzy = _best_per_node(
        r for r in lexical if r.match_kind not in EXACT_MATCH_KINDS
    )
    vector = _best_per_node(semantic)

    all_reasons: dict[UUID, list[str]] = {}
    for result in [*lexical, *semantic]:
        all_reasons.setdefault(result.node_id, []).extend(result.reasons)

    def finish(result: NodeSearchResult, *others: NodeSearchResult | None):
        identifiers = result.identifiers or next(
            (other.identifiers for other in others if other and other.identifiers), []
        )
        return replace(
            result,
            identifiers=identifiers,
            reasons=list(dict.fromkeys(all_reasons[result.node_id])),
        )

    pinned = [
        finish(result, fuzzy.get(node_id), vector.get(node_id))
        for node_id, result in sorted(
            exact.items(),
            key=lambda item: (-item[1].score, item[1].title.casefold(), str(item[0])),
        )
    ]

    fuzzy_ranks = {node_id: rank for rank, node_id in enumerate(fuzzy, start=1)}
    vector_ranks = {node_id: rank for rank, node_id in enumerate(vector, start=1)}
    fused: list[tuple[float, bool, NodeSearchResult]] = []
    for node_id in dict.fromkeys([*fuzzy, *vector]):
        if node_id in exact:
            continue
        fuzzy_rank = fuzzy_ranks.get(node_id)
        vector_rank = vector_ranks.get(node_id)
        rrf = sum(1 / (k + rank) for rank in (fuzzy_rank, vector_rank) if rank)
        # Report the mechanism that ranked the node higher; lexical wins ties.
        if fuzzy_rank is not None and (vector_rank is None or fuzzy_rank <= vector_rank):
            primary, other = fuzzy[node_id], vector.get(node_id)
        else:
            primary, other = vector[node_id], fuzzy.get(node_id)
        fused.append((rrf, primary is vector.get(node_id), finish(primary, other)))
    # Equal fused scores (e.g. each node first in one list) prefer lexical
    # matches, which are easier to explain, rather than comparing raw scores
    # across scales.
    fused.sort(
        key=lambda item: (
            -item[0],
            item[1],
            -item[2].score,
            item[2].title.casefold(),
            str(item[2].node_id),
        )
    )
    return [*pinned, *(result for _, _, result in fused)][:limit]


def _best_per_node(results) -> dict[UUID, NodeSearchResult]:
    """Best result per node, in descending score order."""

    best: dict[UUID, NodeSearchResult] = {}
    for result in sorted(results, key=lambda result: -result.score):
        best.setdefault(result.node_id, result)
    return best
