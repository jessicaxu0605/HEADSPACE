"""PostgreSQL retrieval for lexical and projection-based node search."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

import psycopg

from app.db.postgres import get_async_connection
from app.graph.queries.nearest_node_projections.sql.postgres import nearest_node_projections
from app.graph.queries.node_search import NodeSearchResult
from app.workflows.ingest.models import NodeType, ProposedExternalId


logger = logging.getLogger(__name__)

_PROJECTION_REASONS = {
    "identity": "query describes this {node_type}",
    "profile": "query matches what this {node_type} is associated with",
}


class PostgresNodeSearchRepository:
    """Read lexical and vector-search candidates from PostgreSQL."""

    async def search_lexical(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        aliases: list[str],
        external_ids: list[ProposedExternalId],
        limit: int,
        fuzzy_threshold: float,
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
                    "fuzzy_threshold": fuzzy_threshold,
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

    async def search_semantic(
        self,
        *,
        workspace_id: UUID,
        vector: Sequence[float],
        embedding_model: str,
        node_type: NodeType | None,
        limit: int,
        min_similarity: float,
    ) -> list[NodeSearchResult]:
        try:
            matches = await nearest_node_projections(
                workspace_id=workspace_id,
                vector=vector,
                embedding_model=embedding_model,
                projection_types=("identity", "profile"),
                node_type=node_type,
                limit=limit,
                min_similarity=min_similarity,
            )
            if not matches:
                return []
            nodes = await _load_nodes(
                workspace_id, [match.node_id for match in matches]
            )
        except psycopg.errors.UndefinedTable as exc:
            logger.warning("Semantic node search tables unavailable: %s", exc)
            return []

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
            GROUP BY n.id, n.node_type, n.name
            """,
            (workspace_id, node_ids),
        )
        rows = await cursor.fetchall()
    return {row["node_id"]: row for row in rows}
