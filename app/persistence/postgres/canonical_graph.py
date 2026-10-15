"""PostgreSQL-backed canonical graph reads for create-or-resolve."""

from __future__ import annotations

from uuid import UUID

from app.db import get_async_connection, get_connection
from app.workflows.ingest.relation_types import RelationDefinition
from app.workflows.ingest.resolve_proposed_subgraph import (
    CanonicalEdge,
    CanonicalNode,
    NodeCandidate,
    RelationType,
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.ingest.types import NodeType, ProposedExternalId, ProposedNode


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
    scorer. Interactive search uses one bounded, index-backed SQL query until
    the documented search projections are available.
    """

    def __init__(self, *, fuzzy_candidate_threshold: float = 0.50) -> None:
        self.fuzzy_candidate_threshold = fuzzy_candidate_threshold

    async def search_node_candidates(
        self,
        *,
        workspace_id: UUID,
        query: str,
        node_type: NodeType | None,
        aliases: list[str],
        external_ids: list[ProposedExternalId],
        limit: int,
    ) -> list[NodeCandidate]:
        """Rank an interactive node search in one bounded SQL statement."""

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
                    >= %(fuzzy_candidate_threshold)s

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
                    >= %(fuzzy_candidate_threshold)s
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
                    "fuzzy_candidate_threshold": self.fuzzy_candidate_threshold,
                    "limit": limit,
                },
            )
            rows = await cursor.fetchall()

        return [
            NodeCandidate(
                node=CanonicalNode(
                    node_id=row["node_id"],
                    node_type=row["node_type"],
                    title=row["title"],
                    identifiers=row["identifiers"],
                ),
                match_kind=row["match_kind"],
                match_score=row["match_score"],
                reasons=[row["reason"]],
            )
            for row in rows
        ]

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
        return await scorer.find_node_candidates(
            workspace_id=workspace_id,
            proposed_node=proposed_node,
        )

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
