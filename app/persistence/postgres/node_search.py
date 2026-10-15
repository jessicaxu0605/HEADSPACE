"""PostgreSQL-backed client node search (lexical + projection vectors)."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import psycopg

from app.db import get_async_connection
from app.embeddings import EmbeddingError
from app.persistence.postgres.projection_search import nearest_node_projections
from app.search.node_search import (
    DEFAULT_FUZZY_THRESHOLD,
    DEFAULT_SEMANTIC_THRESHOLD,
    MIN_FUSION_DEPTH,
    NodeSearchResult,
    QueryEmbedder,
    merge_search_results,
)
from app.workflows.ingest.types import NodeType, ProposedExternalId


logger = logging.getLogger(__name__)

_PROJECTION_REASONS = {
    "identity": "query describes this {node_type}",
    "profile": "query matches what this {node_type} is associated with",
}


class PostgresNodeSearchStore:
    """Rank nodes by exact matches, then RRF of fuzzy and projection similarity.

    Without an embedder, or when Ollama or the projection tables are
    unavailable, search falls back to lexical matches only.
    """

    def __init__(
        self,
        *,
        embedder: QueryEmbedder | None = None,
        fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ) -> None:
        self.embedder = embedder
        self.fuzzy_threshold = fuzzy_threshold
        self.semantic_threshold = semantic_threshold

    async def search_nodes(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        aliases: list[str],
        external_ids: list[ProposedExternalId],
        limit: int,
    ) -> list[NodeSearchResult]:
        depth = max(limit * 2, MIN_FUSION_DEPTH)
        lexical, semantic = await asyncio.gather(
            self._search_lexical(
                workspace_id=workspace_id,
                query=query,
                node_type=node_type,
                aliases=aliases,
                external_ids=external_ids,
                limit=depth,
            ),
            self._search_semantic(
                workspace_id=workspace_id,
                query=query,
                node_type=node_type,
                limit=depth,
            ),
        )
        return merge_search_results(lexical, semantic, limit=limit)

    async def _search_lexical(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        aliases: list[str],
        external_ids: list[ProposedExternalId],
        limit: int,
    ) -> list[NodeSearchResult]:
        """Exact and trigram-fuzzy matches in one bounded SQL statement."""

        names = list(dict.fromkeys([query, *aliases]))
        providers = [item.provider for item in external_ids]
        provider_external_ids = [item.external_id for item in external_ids]
        node_type_filter = (
            "AND n.node_type = %(node_type)s" if node_type is not None else ""
        )
        sql = f"""
            WITH input_names AS (
                SELECT DISTINCT lower(btrim(value)) AS name
                FROM unnest(%(names)s::text[]) AS value
                WHERE btrim(value) <> ''
            ),
            input_external_ids AS (
                SELECT lower(btrim(provider)) AS provider, external_id
                FROM unnest(
                    %(providers)s::text[],
                    %(provider_external_ids)s::text[]
                ) AS item(provider, external_id)
            ),
            matches AS (
                SELECT
                    e.node_id,
                    'external_id'::text AS match_kind,
                    1.0::double precision AS match_score,
                    1 AS match_priority,
                    'matching ' || x.provider || ' external ID' AS reason
                FROM input_external_ids AS x
                JOIN node_external_id AS e
                  ON e.workspace_id = %(workspace_id)s
                 AND lower(e.provider) = x.provider
                 AND e.external_id = x.external_id
                 AND e.deleted_at IS NULL
                JOIN memory_node AS n
                  ON n.workspace_id = e.workspace_id
                 AND n.id = e.node_id
                 AND n.deleted_at IS NULL
                 AND n.merged_into_node_id IS NULL
                 {node_type_filter}

                UNION ALL

                SELECT
                    i.node_id,
                    'identifier'::text,
                    0.99::double precision,
                    2,
                    'exact identifier match: ' || quote_literal(i.identifier_text)
                FROM input_names AS q
                JOIN node_identifier AS i
                  ON i.workspace_id = %(workspace_id)s
                 AND i.scope = 'global'
                 AND lower(i.identifier_text) = q.name
                 AND i.deleted_at IS NULL
                JOIN memory_node AS n
                  ON n.workspace_id = i.workspace_id
                 AND n.id = i.node_id
                 AND n.deleted_at IS NULL
                 AND n.merged_into_node_id IS NULL
                 {node_type_filter}

                UNION ALL

                SELECT
                    n.id,
                    'normalized_title'::text,
                    0.98::double precision,
                    3,
                    'exact normalized title match: ' || quote_literal(n.name)
                FROM input_names AS q
                JOIN memory_node AS n
                  ON n.workspace_id = %(workspace_id)s
                 AND lower(n.name) = q.name
                 AND n.deleted_at IS NULL
                 AND n.merged_into_node_id IS NULL
                 {node_type_filter}

                UNION ALL

                SELECT
                    n.id,
                    'fuzzy'::text,
                    similarity(lower(n.name), q.name)::double precision,
                    4,
                    quote_literal(q.name) || ' is lexically similar to '
                        || quote_literal(n.name)
                FROM input_names AS q
                JOIN memory_node AS n
                  ON n.workspace_id = %(workspace_id)s
                 AND lower(n.name) %% q.name
                 AND lower(n.name) <> q.name
                 AND n.deleted_at IS NULL
                 AND n.merged_into_node_id IS NULL
                 {node_type_filter}
                WHERE similarity(lower(n.name), q.name)
                    >= %(fuzzy_threshold)s

                UNION ALL

                SELECT
                    i.node_id,
                    'fuzzy'::text,
                    similarity(lower(i.identifier_text), q.name)::double precision,
                    5,
                    quote_literal(q.name) || ' is lexically similar to '
                        || quote_literal(i.identifier_text)
                FROM input_names AS q
                JOIN node_identifier AS i
                  ON i.workspace_id = %(workspace_id)s
                 AND i.scope = 'global'
                 AND lower(i.identifier_text) %% q.name
                 AND lower(i.identifier_text) <> q.name
                 AND i.deleted_at IS NULL
                JOIN memory_node AS n
                  ON n.workspace_id = i.workspace_id
                 AND n.id = i.node_id
                 AND n.deleted_at IS NULL
                 AND n.merged_into_node_id IS NULL
                 {node_type_filter}
                WHERE similarity(lower(i.identifier_text), q.name)
                    >= %(fuzzy_threshold)s
            ),
            best_match AS (
                SELECT DISTINCT ON (node_id)
                    node_id,
                    match_kind,
                    match_score,
                    reason
                FROM matches
                ORDER BY node_id, match_score DESC, match_priority
            )
            SELECT
                n.id AS node_id,
                n.node_type,
                n.name AS title,
                COALESCE(i.identifiers, ARRAY[]::text[]) AS identifiers,
                b.match_kind,
                b.match_score,
                b.reason
            FROM best_match AS b
            JOIN memory_node AS n
              ON n.workspace_id = %(workspace_id)s
             AND n.id = b.node_id
            LEFT JOIN LATERAL (
                SELECT array_agg(
                    DISTINCT ni.identifier_text ORDER BY ni.identifier_text
                ) AS identifiers
                FROM node_identifier AS ni
                WHERE ni.workspace_id = n.workspace_id
                  AND ni.node_id = n.id
                  AND ni.deleted_at IS NULL
            ) AS i ON true
            ORDER BY
                b.match_score DESC,
                lower(n.name),
                n.id
            LIMIT %(limit)s
        """

        async with get_async_connection() as conn:
            cursor = await conn.execute(
                sql,
                {
                    "workspace_id": workspace_id,
                    "names": names,
                    "providers": providers,
                    "provider_external_ids": provider_external_ids,
                    "node_type": node_type,
                    "fuzzy_threshold": self.fuzzy_threshold,
                    "limit": limit,
                },
            )
            rows = await cursor.fetchall()

        return [
            NodeSearchResult(
                node_id=row["node_id"],
                node_type=row["node_type"],
                title=row["title"],
                match_kind=row["match_kind"],
                score=row["match_score"],
                identifiers=row["identifiers"],
                reasons=[row["reason"]],
            )
            for row in rows
        ]

    async def _search_semantic(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        limit: int,
    ) -> list[NodeSearchResult]:
        if self.embedder is None:
            return []
        try:
            vector = await self.embedder.embed_query(query)
            matches = await nearest_node_projections(
                workspace_id=workspace_id,
                vector=vector,
                embedding_model=self.embedder.model,
                projection_types=("identity", "profile"),
                node_type=node_type,
                # Each node can match through both projections.
                limit=limit * 2,
                min_similarity=self.semantic_threshold,
            )
        except (EmbeddingError, psycopg.errors.UndefinedTable) as exc:
            logger.warning("Semantic node search unavailable: %s", exc)
            return []
        if not matches:
            return []

        nodes = await _load_nodes(workspace_id, [match.node_id for match in matches])
        results = []
        for match in matches:
            node = nodes.get(match.node_id)
            if node is None:
                continue
            reason = _PROJECTION_REASONS[match.projection_type].format(
                node_type=node["node_type"]
            )
            results.append(
                NodeSearchResult(
                    node_id=match.node_id,
                    node_type=node["node_type"],
                    title=node["title"],
                    match_kind=match.projection_type,
                    score=match.similarity,
                    identifiers=node["identifiers"],
                    reasons=[f"{reason} ({match.similarity:.2f})"],
                )
            )
        return results


async def _load_nodes(workspace_id: UUID, node_ids: list[UUID]) -> dict[UUID, dict]:
    async with get_async_connection() as conn:
        cursor = await conn.execute(
            """
            SELECT
                n.id AS node_id,
                n.node_type,
                n.name AS title,
                COALESCE(
                    array_agg(DISTINCT i.identifier_text ORDER BY i.identifier_text)
                        FILTER (WHERE i.identifier_text IS NOT NULL),
                    ARRAY[]::text[]
                ) AS identifiers
            FROM memory_node AS n
            LEFT JOIN node_identifier AS i
              ON i.workspace_id = n.workspace_id
             AND i.node_id = n.id
             AND i.deleted_at IS NULL
            WHERE n.workspace_id = %s AND n.id = ANY(%s)
            GROUP BY n.id
            """,
            (workspace_id, node_ids),
        )
        rows = await cursor.fetchall()
    return {row["node_id"]: row for row in rows}
