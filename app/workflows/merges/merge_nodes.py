"""Merge one canonical node into another.

Not part of ingestion: resolution only binds proposed nodes to canonical ones.
This is kept for an explicit merge flow (a reviewed duplicate or a user action).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.graph.edge_helpers import combine_edge_details
from app.workflows.summaries.refresh_summaries import enqueue_summary_refresh


class NodeMergeError(RuntimeError):
    """The nodes cannot be merged: one is inactive or their types differ."""


def merge_canonical_nodes(
    conn: Any,
    *,
    workspace_id: UUID,
    from_node_id: UUID,
    into_node_id: UUID,
) -> tuple[set[UUID], set[UUID]]:
    """Move durable identity and graph records, then retire the duplicate node.

    Runs inside the caller's transaction. Returns the edges the merge touched
    and, of those, the ones it archived as duplicates or self-edges.
    """

    if from_node_id == into_node_id:
        return set(), set()
    affected_edge_ids: set[UUID] = set()
    archived_edge_ids: set[UUID] = set()
    rows = conn.execute(
        """
        SELECT id, node_type
        FROM memory_node
        WHERE workspace_id = %s
          AND id IN (%s, %s)
          AND deleted_at IS NULL
          AND merged_into_node_id IS NULL
        FOR UPDATE
        """,
        (workspace_id, from_node_id, into_node_id),
    ).fetchall()
    node_types = {row["id"]: row["node_type"] for row in rows}
    if set(node_types) != {from_node_id, into_node_id}:
        raise NodeMergeError(
            "A node selected for merging is no longer active."
        )
    if node_types[from_node_id] != node_types[into_node_id]:
        raise NodeMergeError(
            "Canonical nodes of different types cannot be merged."
        )

    conn.execute(
        """
        UPDATE node_identifier AS source
        SET deleted_at = now(), deleted_reason = 'merged_duplicate'
        WHERE source.workspace_id = %s
          AND source.node_id = %s
          AND source.deleted_at IS NULL
          AND EXISTS (
              SELECT 1 FROM node_identifier AS target
              WHERE target.workspace_id = source.workspace_id
                AND target.node_id = %s
                AND target.scope = source.scope
                AND lower(target.identifier_text) = lower(source.identifier_text)
                AND target.deleted_at IS NULL
          )
        """,
        (workspace_id, from_node_id, into_node_id),
    )
    conn.execute(
        """
        UPDATE node_identifier
        SET node_id = %s, updated_at = now()
        WHERE workspace_id = %s AND node_id = %s AND deleted_at IS NULL
        """,
        (into_node_id, workspace_id, from_node_id),
    )
    conn.execute(
        """
        UPDATE node_external_id
        SET node_id = %s
        WHERE workspace_id = %s AND node_id = %s AND deleted_at IS NULL
        """,
        (into_node_id, workspace_id, from_node_id),
    )
    conn.execute(
        """
        DELETE FROM memory_evidence_annotation AS source
        USING memory_evidence_annotation AS target
        WHERE source.workspace_id = %s
          AND source.node_id = %s
          AND target.workspace_id = source.workspace_id
          AND target.evidence_id = source.evidence_id
          AND target."start" = source."start"
          AND target.length = source.length
          AND target.node_id = %s
        """,
        (workspace_id, from_node_id, into_node_id),
    )
    conn.execute(
        """
        UPDATE memory_evidence_annotation
        SET node_id = %s
        WHERE workspace_id = %s AND node_id = %s
        """,
        (into_node_id, workspace_id, from_node_id),
    )

    edges = conn.execute(
        """
        SELECT
            edge.id,
            edge.from_node_id,
            edge.to_node_id,
            edge.relation_type_id,
            edge.confidence,
            edge.strength,
            edge.details,
            relation.is_symmetric
        FROM memory_edge AS edge
        JOIN relation_type AS relation ON relation.id = edge.relation_type_id
        WHERE edge.workspace_id = %s
          AND edge.deleted_at IS NULL
          AND (edge.from_node_id = %s OR edge.to_node_id = %s)
        FOR UPDATE
        """,
        (workspace_id, from_node_id, from_node_id),
    ).fetchall()
    for edge in edges:
        affected_edge_ids.add(edge["id"])
        new_from = (
            into_node_id
            if edge["from_node_id"] == from_node_id
            else edge["from_node_id"]
        )
        new_to = (
            into_node_id
            if edge["to_node_id"] == from_node_id
            else edge["to_node_id"]
        )
        if edge["is_symmetric"] and str(new_from) > str(new_to):
            new_from, new_to = new_to, new_from
        if new_from == new_to:
            conn.execute(
                """
                UPDATE memory_edge
                SET deleted_at = now(), deleted_reason = 'merged_self_edge'
                WHERE workspace_id = %s AND id = %s
                """,
                (workspace_id, edge["id"]),
            )
            archived_edge_ids.add(edge["id"])
            continue
        existing = conn.execute(
            """
            SELECT id, confidence, strength, details
            FROM memory_edge
            WHERE workspace_id = %s
              AND from_node_id = %s
              AND to_node_id = %s
              AND relation_type_id = %s
              AND deleted_at IS NULL
              AND id <> %s
            FOR UPDATE
            """,
            (workspace_id, new_from, new_to, edge["relation_type_id"], edge["id"]),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                UPDATE memory_edge
                SET from_node_id = %s, to_node_id = %s, updated_at = now()
                WHERE workspace_id = %s AND id = %s
                """,
                (new_from, new_to, workspace_id, edge["id"]),
            )
            continue
        affected_edge_ids.add(existing["id"])
        conn.execute(
            """
            DELETE FROM memory_evidence AS source
            USING memory_evidence AS target
            WHERE source.workspace_id = %s
              AND source.edge_id = %s
              AND target.workspace_id = source.workspace_id
              AND target.edge_id = %s
              AND target.source_id = source.source_id
              AND target.raw_content = source.raw_content
            """,
            (workspace_id, edge["id"], existing["id"]),
        )
        conn.execute(
            """
            UPDATE memory_evidence
            SET edge_id = %s
            WHERE workspace_id = %s AND edge_id = %s
            """,
            (existing["id"], workspace_id, edge["id"]),
        )
        conn.execute(
            """
            UPDATE memory_edge
            SET confidence = 1 - ((1 - confidence) * (1 - %s)),
                strength = 1 - ((1 - strength) * (1 - %s)),
                details = %s,
                updated_at = now()
            WHERE workspace_id = %s AND id = %s
            """,
            (
                edge["confidence"],
                edge["strength"],
                combine_edge_details(existing["details"], edge["details"]),
                workspace_id,
                existing["id"],
            ),
        )
        conn.execute(
            """
            UPDATE memory_edge
            SET deleted_at = now(), deleted_reason = 'merged_duplicate_edge'
            WHERE workspace_id = %s AND id = %s
            """,
            (workspace_id, edge["id"]),
        )
        archived_edge_ids.add(edge["id"])

    # Keep the duplicate's summary: use it when the surviving node has none,
    # otherwise queue it to be merged, along with any refresh still pending.
    summaries = conn.execute(
        """
        SELECT source.summary AS source_summary, target.summary AS target_summary
        FROM memory_node AS source, memory_node AS target
        WHERE source.workspace_id = %s AND source.id = %s
          AND target.workspace_id = source.workspace_id AND target.id = %s
        """,
        (workspace_id, from_node_id, into_node_id),
    ).fetchone()
    pending_summaries = [
        summary
        for row in conn.execute(
            """
            DELETE FROM node_summary_refresh_queue
            WHERE workspace_id = %s AND node_id = %s AND status = 'pending'
            RETURNING incoming_summaries
            """,
            (workspace_id, from_node_id),
        ).fetchall()
        for summary in row["incoming_summaries"]
    ]
    source_summary = (summaries["source_summary"] or "").strip()
    if source_summary and not (summaries["target_summary"] or "").strip():
        conn.execute(
            """
            UPDATE memory_node SET summary = %s, updated_at = now()
            WHERE workspace_id = %s AND id = %s
            """,
            (source_summary, workspace_id, into_node_id),
        )
    elif source_summary and source_summary != summaries["target_summary"].strip():
        pending_summaries.insert(0, source_summary)
    if pending_summaries:
        enqueue_summary_refresh(
            conn,
            workspace_id=workspace_id,
            node_id=into_node_id,
            incoming_summaries=pending_summaries,
        )

    # Fill the surviving node's missing type-specific fields from the duplicate.
    conn.execute(
        """
        UPDATE memory_node AS target
        SET type_specific = source.type_specific
              || COALESCE(target.type_specific, '{}'::jsonb),
            updated_at = now()
        FROM memory_node AS source
        WHERE target.workspace_id = %s
          AND target.id = %s
          AND source.workspace_id = target.workspace_id
          AND source.id = %s
          AND source.type_specific IS NOT NULL
        """,
        (workspace_id, into_node_id, from_node_id),
    )
    conn.execute(
        """
        UPDATE memory_node
        SET merged_into_node_id = %s,
            updated_at = now()
        WHERE workspace_id = %s AND id = %s
        """,
        (into_node_id, workspace_id, from_node_id),
    )
    return affected_edge_ids, archived_edge_ids
