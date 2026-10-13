"""PostgreSQL-backed canonical graph reads for create-or-resolve."""

from __future__ import annotations

from uuid import UUID

from app.db import get_connection
from app.workflows.resolve_proposed_subgraph import (
    CanonicalEdge,
    CanonicalNode,
    NodeCandidate,
    RelationType,
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.types import ProposedExternalId, ProposedNode


class PostgresCanonicalGraphResolverStore:
    """Read current workspace graph state directly from PostgreSQL.

    Candidate scoring intentionally delegates to the prototype lexical scorer.
    The persistence adapter can later replace this snapshot query with search
    projections without changing the resolution policy.
    """

    def __init__(self, *, fuzzy_candidate_threshold: float = 0.50) -> None:
        self.fuzzy_candidate_threshold = fuzzy_candidate_threshold

    async def find_node_candidates(
        self,
        *,
        workspace_id: UUID,
        proposed_node: ProposedNode,
    ) -> list[NodeCandidate]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    n.id AS node_id,
                    n.node_type,
                    n.name AS title,
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
            ).fetchall()

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

    async def find_edge(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        to_node_id: UUID,
        relation_type: str,
    ) -> CanonicalEdge | None:
        with get_connection() as conn:
            row = conn.execute(
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
                  AND e.to_node_id = %s
                  AND r.name = %s
                  AND e.deleted_at IS NULL
                """,
                (workspace_id, from_node_id, to_node_id, relation_type),
            ).fetchone()
        return CanonicalEdge(**row) if row is not None else None

    async def get_relation_type(
        self,
        *,
        workspace_id: UUID,
        relation_type: str,
    ) -> RelationType | None:
        del workspace_id
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT name, is_exclusive, can_change_over_time
                FROM relation_type
                WHERE name = %s
                """,
                (relation_type,),
            ).fetchone()
        return RelationType(**row) if row is not None else None

    async def find_active_outgoing_edges(
        self,
        *,
        workspace_id: UUID,
        from_node_id: UUID,
        relation_type: str,
    ) -> list[CanonicalEdge]:
        with get_connection() as conn:
            rows = conn.execute(
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
            ).fetchall()
        return [CanonicalEdge(**row) for row in rows]
