"""PostgreSQL adapter for canonical graph resolution reads."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

import psycopg

from app.db.postgres import get_async_connection, get_connection
from app.graph.queries.nearest_node_projections.sql.postgres import (
    nearest_node_projections,
)
from app.integrations.ollama.embeddings import EmbeddingError
from app.workflows.ingest.resolution.identity_candidates import (
    IDENTITY_EMBEDDING_LIMIT,
    IDENTITY_EMBEDDING_THRESHOLD,
    identity_embedding_candidate,
    merge_identity_candidates,
    needs_identity_embedding,
    proposed_identity_text,
)
from app.workflows.ingest.relation_types import RelationDefinition
from app.workflows.ingest.resolution.service import (
    CanonicalEdge,
    CanonicalNode,
    NodeCandidate,
    RelationType,
)
from app.workflows.ingest.resolution.in_memory import (
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.ingest.models import ProposedExternalId, ProposedNode


logger = logging.getLogger(__name__)


class DocumentEmbedder(Protocol):
    """Document-vector operation required by identity resolution."""

    model: str

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


_RELATION_COLUMNS = """
    name,
    inverse_name,
    description,
    connector,
    past_connector,
    future_connector,
    is_symmetric,
    is_exclusive,
    can_change_over_time,
    allowed_from_types,
    allowed_to_types,
    is_extractable,
    parent_name
"""


def _relation_from_row(row: dict) -> RelationDefinition:
    return RelationDefinition(
        name=row["name"],
        inverse_name=row["inverse_name"],
        description=row["description"] or "",
        connector=row["connector"],
        past_connector=row["past_connector"],
        future_connector=row["future_connector"],
        is_symmetric=row["is_symmetric"],
        is_exclusive=row["is_exclusive"],
        can_change_over_time=row["can_change_over_time"],
        allowed_from_types=tuple(row["allowed_from_types"]),
        allowed_to_types=tuple(row["allowed_to_types"]),
        is_extractable=row["is_extractable"],
        parent_name=row["parent_name"],
    )


def load_active_relation_types() -> list[RelationDefinition]:
    """Load the active relation catalog used to construct extraction schemas."""

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_RELATION_COLUMNS}
            FROM relation_type
            WHERE is_active
            ORDER BY name
            """
        ).fetchall()
    return [_relation_from_row(row) for row in rows]


class PostgresCanonicalGraphResolverStore:
    """Read current workspace graph state directly from PostgreSQL.

    Ingestion resolution delegates snapshot scoring to the prototype lexical
    scorer, then adds identity-projection candidates when an embedder is given
    and lexical matching found no exact match. Client search is separate, in
    ``app.graph.queries.node_search.sql.postgres``.
    """

    def __init__(
        self,
        *,
        fuzzy_candidate_threshold: float = 0.50,
        embedder: DocumentEmbedder | None = None,
    ) -> None:
        self.fuzzy_candidate_threshold = fuzzy_candidate_threshold
        self.embedder = embedder

    async def find_node_candidates(
        self,
        *,
        workspace_id: UUID,
        proposed_node: ProposedNode,
    ) -> list[NodeCandidate]:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    n.id AS node_id,
                    n.node_type,
                    n.name AS title,
                    n.type_specific,
                    n.summary,
                    i.identifier_text,
                    e.provider,
                    e.external_id
                FROM memory_node AS n
                LEFT JOIN node_identifier AS i
                  ON i.workspace_id = n.workspace_id
                 AND i.node_id = n.id
                 AND i.deleted_at IS NULL
                LEFT JOIN node_external_id AS e
                  ON e.workspace_id = n.workspace_id
                 AND e.node_id = n.id
                 AND e.deleted_at IS NULL
                WHERE n.workspace_id = %s
                  AND n.deleted_at IS NULL
                  AND n.merged_into_node_id IS NULL
                ORDER BY n.id
                """,
                (workspace_id,),
            )
            rows = await cursor.fetchall()

        nodes: dict[UUID, CanonicalNode] = {}
        identifier_sets: dict[UUID, set[str]] = {}
        external_id_sets: dict[UUID, set[tuple[str, str]]] = {}
        for row in rows:
            node_id = row["node_id"]
            if node_id not in nodes:
                nodes[node_id] = CanonicalNode(
                    node_id=node_id,
                    node_type=row["node_type"],
                    title=row["title"],
                    identifiers=[],
                    external_ids=[],
                    type_specific=row["type_specific"],
                    summary=row["summary"],
                )
                identifier_sets[node_id] = set()
                external_id_sets[node_id] = set()
            if row["identifier_text"] is not None:
                identifier_sets[node_id].add(row["identifier_text"])
            if row["provider"] is not None and row["external_id"] is not None:
                external_id_sets[node_id].add((row["provider"], row["external_id"]))

        snapshot: list[CanonicalNode] = []
        for node_id, node in nodes.items():
            snapshot.append(
                CanonicalNode(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    title=node.title,
                    identifiers=sorted(identifier_sets[node_id]),
                    external_ids=[
                        ProposedExternalId(provider, external_id)
                        for provider, external_id in sorted(external_id_sets[node_id])
                    ],
                    type_specific=node.type_specific,
                    summary=node.summary,
                )
            )
        scorer = SimpleCanonicalGraphResolverStore(
            nodes=snapshot,
            fuzzy_candidate_threshold=self.fuzzy_candidate_threshold,
        )
        lexical = await scorer.find_node_candidates(
            workspace_id=workspace_id,
            proposed_node=proposed_node,
        )
        if self.embedder is None or not needs_identity_embedding(lexical):
            return lexical
        embedding = await self._identity_embedding_candidates(
            workspace_id=workspace_id,
            proposed_node=proposed_node,
            snapshot={node.node_id: node for node in snapshot},
        )
        return merge_identity_candidates(lexical, embedding)

    async def _identity_embedding_candidates(
        self,
        *,
        workspace_id: UUID,
        proposed_node: ProposedNode,
        snapshot: dict[UUID, CanonicalNode],
    ) -> list[NodeCandidate]:
        assert self.embedder is not None
        try:
            # Document prefix on both sides: this is a symmetric comparison
            # against stored identity projections, not a query.
            [vector] = await self.embedder.embed_documents(
                [proposed_identity_text(proposed_node)]
            )
            matches = await nearest_node_projections(
                workspace_id=workspace_id,
                vector=vector,
                embedding_model=self.embedder.model,
                projection_types=("identity",),
                node_type=proposed_node.node_type,
                limit=IDENTITY_EMBEDDING_LIMIT,
                min_similarity=IDENTITY_EMBEDDING_THRESHOLD,
            )
        except (EmbeddingError, psycopg.errors.UndefinedTable) as exc:
            logger.warning("Identity embedding candidates unavailable: %s", exc)
            return []
        candidates = []
        for match in matches:
            canonical_node = snapshot.get(match.node_id)
            if canonical_node is None:
                continue
            candidate = identity_embedding_candidate(
                proposed_node, canonical_node, match.similarity
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def find_edges_between(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
        other_node_id: UUID,
    ) -> list[CanonicalEdge]:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    e.id AS edge_id,
                    e.from_node_id,
                    e.to_node_id,
                    r.name AS relation_type,
                    e.confidence,
                    e.strength
                FROM memory_edge AS e
                JOIN relation_type AS r ON r.id = e.relation_type_id
                WHERE e.workspace_id = %(workspace_id)s
                  AND (
                    (e.from_node_id = %(node_id)s AND e.to_node_id = %(other_node_id)s)
                    OR (e.from_node_id = %(other_node_id)s AND e.to_node_id = %(node_id)s)
                  )
                  AND e.deleted_at IS NULL
                ORDER BY e.id
                """,
                {
                    "workspace_id": workspace_id,
                    "node_id": node_id,
                    "other_node_id": other_node_id,
                },
            )
            rows = await cursor.fetchall()
        return [CanonicalEdge(**row) for row in rows]

    async def list_relation_types(
        self,
        *,
        workspace_id: UUID,
    ) -> list[RelationType]:
        del workspace_id
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT {_RELATION_COLUMNS}
                FROM relation_type
                WHERE is_active
                ORDER BY name
                """
            )
            rows = await cursor.fetchall()
        return [_relation_from_row(row) for row in rows]

    async def find_neighbor_node_ids(
        self,
        *,
        workspace_id: UUID,
        node_id: UUID,
    ) -> set[UUID]:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT CASE
                    WHEN from_node_id = %s THEN to_node_id
                    ELSE from_node_id
                END AS neighbor_id
                FROM memory_edge
                WHERE workspace_id = %s
                  AND deleted_at IS NULL
                  AND (from_node_id = %s OR to_node_id = %s)
                """,
                (node_id, workspace_id, node_id, node_id),
            )
            rows = await cursor.fetchall()
        return {row["neighbor_id"] for row in rows}

    async def resolve_relation_type(
        self,
        *,
        workspace_id: UUID,
        relation_type: str,
    ) -> tuple[RelationType, bool] | None:
        del workspace_id
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT {_RELATION_COLUMNS}
                FROM relation_type
                WHERE is_active
                  AND (name = %s OR inverse_name = %s)
                ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (relation_type, relation_type, relation_type),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        relation = _relation_from_row(row)
        reverse_endpoints = (
            relation_type == relation.inverse_name and not relation.is_symmetric
        )
        return relation, reverse_endpoints

    async def find_active_outgoing_edges(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        relation_type: str,
    ) -> list[CanonicalEdge]:
        async with get_async_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    e.id AS edge_id,
                    e.from_node_id,
                    e.to_node_id,
                    r.name AS relation_type,
                    e.confidence,
                    e.strength
                FROM memory_edge AS e
                JOIN relation_type AS r ON r.id = e.relation_type_id
                WHERE e.workspace_id = %s
                  AND e.from_node_id = %s
                  AND r.name = %s
                  AND e.deleted_at IS NULL
                ORDER BY e.id
                """,
                (workspace_id, from_node_id, relation_type),
            )
            rows = await cursor.fetchall()
        return [CanonicalEdge(**row) for row in rows]
