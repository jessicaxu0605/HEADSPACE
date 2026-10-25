"""PostgreSQL surfacing sources and subgraph expansion for the landing page.

Each source is a small dataclass with its own knobs and one normalized query.
The enabled queries are combined with ``UNION ALL`` for one discovery round
trip, then redistributed to their source configs for ranking. Add, remove, or
reorder sources in ``DEFAULT_UPCOMING_SOURCES`` and ``DEFAULT_LANDING_SOURCES``;
expansion into the card's subgraph is shared and independent of the source.

Upcoming items are surfaced separately by ``surface_upcoming``: unfinished
tasks and events in the next two weeks, in date order, each with only its
adjacent edges and nodes. They are a list, not a draw, so they skip ranking,
evidence, and related-edge expansion.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.graph.queries.memory_surfacing import (
    DiscoveryQuery,
    EdgeRole,
    ExpansionConfig,
    RankedAnchor,
    SourceConfig,
    SurfaceAnchor,
    SurfacedEdge,
    SurfacedEvidence,
    SurfacedMemory,
    SurfacedNode,
    SurfacingContext,
    SurfacingSource,
    rank_anchors,
)
from app.persistence.postgres.connection import get_async_connection
from app.search.edge_projection import EDGE_PROJECTION_VERSION
from app.search.node_projection import NODE_PROJECTION_VERSION


# --- SQL fragments -----------------------------------------------------------


def _iso_date(expr: str) -> str:
    """Cast a type_specific ISO string to a date, or NULL if it is not one."""

    return (
        f"(CASE WHEN {expr} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
        f"THEN left({expr}, 10)::date END)"
    )


def _iso_timestamp(expr: str) -> str:
    return (
        f"(CASE WHEN {expr} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
        f"THEN ({expr})::timestamptz END)"
    )


_LIVE_NODE = "{n}.deleted_at IS NULL AND {n}.merged_into_node_id IS NULL"

# Both endpoints of ``e`` must be canonical. Expired edges are kept: a past
# relationship is still a memory, and its connector is rendered in past tense.
_LIVE_ENDPOINTS = f"""
    JOIN memory_node AS f
      ON f.workspace_id = e.workspace_id AND f.id = e.from_node_id
     AND {_LIVE_NODE.format(n="f")}
    JOIN memory_node AS t
      ON t.workspace_id = e.workspace_id AND t.id = e.to_node_id
     AND {_LIVE_NODE.format(n="t")}
"""

_NOT_EXCLUDED_EDGE = "NOT (e.id = ANY(%(exclude_edge_ids)s::uuid[]))"

_EDGE_COLUMNS = """
    e.id AS edge_id,
    e.from_node_id,
    e.to_node_id,
    r.name AS relation_type,
    COALESCE(
        CASE
            WHEN e.valid_from IS NOT NULL AND e.valid_from > now()
                THEN r.future_connector
            WHEN e.valid_to IS NOT NULL AND e.valid_to <= now()
                THEN r.past_connector
            ELSE r.connector
        END,
        replace(r.name, '_', ' ')
    ) AS connector,
    e.details,
    e.strength,
    e.confidence,
    CASE
        WHEN e.valid_from IS NOT NULL AND e.valid_from > now() THEN 'scheduled'
        WHEN e.valid_to IS NOT NULL AND e.valid_to <= now() THEN 'expired'
        ELSE 'active'
    END AS status,
    e.valid_from,
    e.valid_to
"""

_HAS_EVIDENCE = """
    EXISTS (
        SELECT 1 FROM memory_evidence AS ev
        WHERE ev.workspace_id = e.workspace_id AND ev.edge_id = e.id
    )
"""


def _params(ctx: SurfacingContext, limit: int, **extra: Any) -> dict[str, Any]:
    return {
        "workspace_id": ctx.workspace_id,
        "now": ctx.now,
        "today": _local_today(ctx),
        "tz": ctx.timezone,
        "exclude_node_ids": list(ctx.exclude_node_ids),
        "exclude_edge_ids": list(ctx.exclude_edge_ids),
        "limit": limit,
        **extra,
    }


def _local_today(ctx: SurfacingContext) -> date:
    return ctx.now.astimezone(ZoneInfo(ctx.timezone)).date()


def _relative_days_sql(offset: str) -> str:
    """SQL expression equivalent of ``relative_days`` for a day offset."""

    return f"""
        CASE
            WHEN {offset} = 0 THEN 'today'
            WHEN {offset} = 1 THEN 'tomorrow'
            WHEN {offset} = -1 THEN 'yesterday'
            WHEN {offset} > 0 THEN 'in ' || {offset} || ' days'
            ELSE (-({offset})) || ' days ago'
        END
    """


async def _fetch_discovery_batches(
    conn: Any, queries: Sequence[DiscoveryQuery]
) -> list[list[SurfaceAnchor]]:
    """Execute modular discovery queries as one UNION and restore their batches."""

    if not queries:
        return []

    branches: list[str] = []
    combined_params: dict[str, Any] = {}
    for index, query in enumerate(queries):
        prefix = f"source_{index}_"

        def namespace(match: re.Match[str]) -> str:
            return f"%({prefix}{match.group(1)})s"

        sql = re.sub(r"%\(([^)]+)\)s", namespace, query.sql)
        branches.append(
            f"""
            SELECT
                {index}::integer AS source_index,
                q.anchor_kind,
                q.anchor_id,
                q.score,
                q.note,
                q.anchor_date
            FROM ({sql}) AS q
            """
        )
        combined_params.update(
            {f"{prefix}{name}": value for name, value in query.params.items()}
        )

    cursor = await conn.execute("\nUNION ALL\n".join(branches), combined_params)
    batches: list[list[SurfaceAnchor]] = [[] for _ in queries]
    for row in await cursor.fetchall():
        index = row["source_index"]
        query = queries[index]
        batches[index].append(
            SurfaceAnchor(
                kind=row["anchor_kind"],
                id=row["anchor_id"],
                reason=query.source_name,
                score=row["score"],
                note=row["note"],
                when=row["anchor_date"],
            )
        )
    return batches


# --- Sources -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenTasks:
    """Unfinished tasks, overdue and soon-due first.

    The due date is ``type_specific.due_at`` or the earliest ``due_on`` /
    ``scheduled_for`` date node, since extraction may produce either.
    """

    name: str = "open_tasks"
    # Tasks with no due date stop surfacing after this long.
    stale_after: timedelta = timedelta(days=30)
    # Due dates this far out score as low as undated tasks.
    horizon_days: int = 14

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        offset = "tasks.due_date - %(today)s"
        due_note = _relative_days_sql(offset)
        return DiscoveryQuery(
            self.name,
            f"""
            WITH tasks AS (
                SELECT
                    n.id,
                    n.created_at,
                    n.type_specific->>'urgency' AS urgency,
                    COALESCE(n.type_specific->>'task_status', 'todo') AS task_status,
                    COALESCE(
                        {_iso_date("n.type_specific->>'due_at'")},
                        due.due_date
                    ) AS due_date
                FROM memory_node AS n
                LEFT JOIN LATERAL (
                    SELECT min({_iso_date(
                        "COALESCE(d.type_specific->>'date_value', d.type_specific->>'start_at')"
                    )}) AS due_date
                    FROM memory_edge AS e
                    JOIN relation_type AS r
                      ON r.id = e.relation_type_id
                     AND r.name IN ('due_on', 'scheduled_for')
                    JOIN memory_node AS d
                      ON d.workspace_id = e.workspace_id
                     AND d.id = e.to_node_id
                     AND {_LIVE_NODE.format(n="d")}
                    WHERE e.workspace_id = n.workspace_id
                      AND e.from_node_id = n.id
                      AND e.deleted_at IS NULL
                ) AS due ON true
                WHERE n.workspace_id = %(workspace_id)s
                  AND n.node_type = 'task'
                  AND {_LIVE_NODE.format(n="n")}
                  AND COALESCE(n.type_specific->>'task_status', 'todo')
                      IN ('todo', 'in_progress', 'blocked')
                  AND n.type_specific->>'completed_at' IS NULL
            )
            SELECT
                'node'::text AS anchor_kind,
                tasks.id AS anchor_id,
                LEAST(
                    GREATEST(
                        CASE
                            WHEN tasks.due_date IS NULL THEN 0.3
                            WHEN tasks.due_date < %(today)s THEN 1.0
                            ELSE GREATEST(
                                0.3,
                                0.95 - 0.65 * ({offset}) / %(horizon_days)s
                            )
                        END
                        + CASE tasks.urgency
                            WHEN 'high' THEN 0.2
                            WHEN 'low' THEN -0.1
                            ELSE 0
                        END
                        - CASE WHEN tasks.task_status = 'blocked' THEN 0.1 ELSE 0 END,
                        0
                    ),
                    1
                )::double precision AS score,
                CASE
                    WHEN tasks.due_date IS NULL THEN
                        CASE WHEN tasks.task_status = 'blocked' THEN 'blocked' END
                    WHEN tasks.due_date < %(today)s THEN
                        'overdue by ' || (-({offset})) || ' day'
                        || CASE WHEN {offset} <> -1 THEN 's' ELSE '' END
                    ELSE 'due ' || ({due_note})
                END AS note,
                tasks.due_date AS anchor_date
            FROM tasks
            WHERE tasks.due_date IS NOT NULL
               OR tasks.created_at >= %(now)s - %(stale_after)s
            ORDER BY tasks.due_date NULLS LAST, tasks.created_at DESC
            LIMIT %(limit)s
            """,
            _params(
                ctx,
                limit,
                stale_after=self.stale_after,
                horizon_days=self.horizon_days,
            ),
        )


@dataclass(frozen=True, slots=True)
class UpcomingEvents:
    """Events whose ``occurs_on`` date is today or within the horizon.

    A range-precision event that has started and not ended counts as today.
    """

    name: str = "upcoming_events"
    horizon_days: int = 14

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        offset = "events.start_date - %(today)s"
        relative = _relative_days_sql(offset)
        return DiscoveryQuery(
            self.name,
            f"""
            WITH events AS (
            SELECT n.id, min(x.start_date) AS start_date
            FROM memory_node AS n
            JOIN memory_edge AS e
              ON e.workspace_id = n.workspace_id
             AND e.from_node_id = n.id
             AND e.deleted_at IS NULL
            JOIN relation_type AS r
              ON r.id = e.relation_type_id AND r.name = 'occurs_on'
            JOIN memory_node AS d
              ON d.workspace_id = e.workspace_id
             AND d.id = e.to_node_id
             AND d.node_type = 'date'
             AND {_LIVE_NODE.format(n="d")}
            CROSS JOIN LATERAL (
                SELECT
                    {_iso_date(
                        "COALESCE(d.type_specific->>'date_value', d.type_specific->>'start_at')"
                    )} AS start_date,
                    {_iso_date("d.type_specific->>'end_at'")} AS end_date
            ) AS x
            WHERE n.workspace_id = %(workspace_id)s
              AND n.node_type = 'event'
              AND {_LIVE_NODE.format(n="n")}
              AND COALESCE(d.type_specific->>'precision', 'day')
                  IN ('day', 'datetime', 'range')
              AND COALESCE(x.end_date, x.start_date) >= %(today)s
              AND x.start_date <= %(today)s + %(horizon_days)s
            GROUP BY n.workspace_id, n.id
            ORDER BY start_date, n.id
            LIMIT %(limit)s
            )
            SELECT
                'node'::text AS anchor_kind,
                events.id AS anchor_id,
                GREATEST(
                    0.3,
                    0.95 - 0.65 * GREATEST({offset}, 0) / %(horizon_days)s
                )::double precision AS score,
                CASE
                    WHEN {offset} < 0 THEN 'happening now'
                    ELSE {relative}
                END AS note,
                GREATEST(events.start_date, %(today)s) AS anchor_date
            FROM events
            """,
            _params(ctx, limit, horizon_days=self.horizon_days),
        )


@dataclass(frozen=True, slots=True)
class OnThisDay:
    """Edges into date nodes whose anniversary falls near today.

    Covers upcoming events and birthdays as well as past years' events. The
    card anchors on the edge, so it shows the event or person, not the bare
    date. Month- and year-precision dates are skipped because they have no day.
    """

    name: str = "on_this_day"
    lookback_days: int = 2
    lookahead_days: int = 7
    # An event's own upcoming occurrence is in the upcoming list; only its
    # anniversaries in later years surface here.
    skip_upcoming_events: bool = True
    # Deadlines are already surfaced by OpenTasks.
    exclude_relations: tuple[str, ...] = ("due_on", "scheduled_for")

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        today = _local_today(ctx)
        # The anniversary nearest today among last, this, and next year, so
        # a window around New Year still matches.
        anniversary = (
            "(dates.dv + make_interval(years => "
            "%(year)s - extract(year FROM dates.dv)::int + k))::date"
        )
        offset = "deduplicated.anniversary - %(today)s"
        relative = _relative_days_sql(offset)
        years_ago = (
            "extract(year FROM deduplicated.anniversary)::int "
            "- extract(year FROM deduplicated.dv)::int"
        )
        return DiscoveryQuery(
            self.name,
            f"""
            WITH dates AS (
                SELECT d.id AS date_node_id, x.dv
                FROM memory_node AS d
                CROSS JOIN LATERAL (
                    SELECT {_iso_date(
                        "COALESCE(d.type_specific->>'date_value', d.type_specific->>'start_at')"
                    )} AS dv
                ) AS x
                WHERE d.workspace_id = %(workspace_id)s
                  AND d.node_type = 'date'
                  AND {_LIVE_NODE.format(n="d")}
                  AND x.dv IS NOT NULL
                  AND COALESCE(d.type_specific->>'precision', 'day')
                      IN ('day', 'datetime', 'range')
            ),
            hits AS (
                SELECT dates.date_node_id, dates.dv, best.anniversary
                FROM dates
                CROSS JOIN LATERAL (
                    SELECT {anniversary} AS anniversary
                    FROM generate_series(-1, 1) AS k
                    ORDER BY abs({anniversary} - %(today)s)
                    LIMIT 1
                ) AS best
                WHERE best.anniversary - %(today)s
                      BETWEEN -%(lookback_days)s AND %(lookahead_days)s
                  AND best.anniversary >= dates.dv
            ),
            raw AS (
            SELECT
                e.id AS edge_id,
                r.name AS relation_type,
                hits.dv,
                hits.anniversary,
                row_number() OVER (
                    ORDER BY
                        abs(hits.anniversary - %(today)s),
                        e.strength * e.confidence DESC,
                        e.id
                ) AS source_order
            FROM hits
            JOIN memory_edge AS e
              ON e.workspace_id = %(workspace_id)s
             AND e.deleted_at IS NULL
             AND hits.date_node_id IN (e.from_node_id, e.to_node_id)
            JOIN relation_type AS r ON r.id = e.relation_type_id
            {_LIVE_ENDPOINTS}
            WHERE NOT (f.node_type = 'date' AND t.node_type = 'date')
              AND r.name <> ALL(%(exclude_relations)s::text[])
              AND {_NOT_EXCLUDED_EDGE}
            ORDER BY
                abs(hits.anniversary - %(today)s),
                e.strength * e.confidence DESC,
                e.id
            LIMIT %(limit)s
            ),
            deduplicated AS (
                SELECT DISTINCT ON (raw.edge_id) raw.*
                FROM raw
                WHERE NOT (
                    %(skip_upcoming_events)s
                    AND raw.relation_type = 'occurs_on'
                    AND extract(year FROM raw.anniversary)::int
                        - extract(year FROM raw.dv)::int = 0
                )
                ORDER BY raw.edge_id, raw.source_order
            )
            SELECT
                'edge'::text AS anchor_kind,
                deduplicated.edge_id AS anchor_id,
                (
                    1 - abs({offset})::double precision
                        / %(score_window)s
                )::double precision AS score,
                CASE
                    WHEN deduplicated.relation_type = 'born_on' THEN
                        'birthday ' || ({relative})
                    WHEN {years_ago} > 0 THEN
                        ({years_ago}) || ' year'
                        || CASE WHEN {years_ago} <> 1 THEN 's' ELSE '' END
                        || ' ago'
                        || CASE
                            WHEN {offset} = 0 THEN ' today'
                            ELSE ' (' || ({relative}) || ')'
                        END
                    ELSE {relative}
                END AS note,
                NULL::date AS anchor_date
            FROM deduplicated
            ORDER BY deduplicated.source_order
            """,
            _params(
                ctx,
                limit,
                year=today.year,
                lookback_days=self.lookback_days,
                lookahead_days=self.lookahead_days,
                exclude_relations=list(self.exclude_relations),
                skip_upcoming_events=self.skip_upcoming_events,
                score_window=max(self.lookback_days, self.lookahead_days) + 1,
            ),
        )


@dataclass(frozen=True, slots=True)
class SourceAnniversaries:
    """Edges evidenced by a source written on this calendar day in a past year.

    Uses ``source_metadata.sent_at`` when present, otherwise ingestion time.
    """

    name: str = "source_anniversary"

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        today = _local_today(ctx)
        years_ago = "%(year)s - extract(year FROM edges.local_date)::int"
        return DiscoveryQuery(
            self.name,
            f"""
            WITH sources AS (
                SELECT s.id, (x.at AT TIME ZONE %(tz)s)::date AS local_date
                FROM memory_source AS s
                CROSS JOIN LATERAL (
                    SELECT COALESCE(
                        {_iso_timestamp("s.source_metadata->>'sent_at'")},
                        s.created_at
                    ) AS at
                ) AS x
                WHERE s.workspace_id = %(workspace_id)s
                  AND to_char(x.at AT TIME ZONE %(tz)s, 'MM-DD') = %(month_day)s
                  AND (x.at AT TIME ZONE %(tz)s)::date < %(today)s
            ),
            edges AS (
                SELECT DISTINCT ON (e.id)
                    e.id AS edge_id,
                    sources.local_date,
                    e.strength,
                    e.confidence
                FROM sources
                JOIN memory_evidence AS ev
                  ON ev.workspace_id = %(workspace_id)s
                 AND ev.source_id = sources.id
                JOIN memory_edge AS e
                  ON e.workspace_id = ev.workspace_id
                 AND e.id = ev.edge_id
                 AND e.deleted_at IS NULL
                {_LIVE_ENDPOINTS}
                WHERE {_NOT_EXCLUDED_EDGE}
                ORDER BY e.id, sources.local_date
            )
            SELECT
                'edge'::text AS anchor_kind,
                edge_id AS anchor_id,
                (0.5 + 0.5 * strength * confidence)::double precision AS score,
                ({years_ago}) || ' year'
                    || CASE WHEN {years_ago} <> 1 THEN 's' ELSE '' END
                    || ' ago today' AS note,
                NULL::date AS anchor_date
            FROM edges
            ORDER BY strength * confidence DESC, edge_id
            LIMIT %(limit)s
            """,
            _params(
                ctx,
                limit,
                month_day=today.strftime("%m-%d"),
                year=today.year,
            ),
        )


@dataclass(frozen=True, slots=True)
class EdgeValidityChanges:
    """Edges that start or end near now ("moves to Toronto next week")."""

    name: str = "starting_or_ending"
    window: timedelta = timedelta(days=7)

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        offset = "(selected.boundary AT TIME ZONE %(tz)s)::date - %(today)s"
        relative = _relative_days_sql(offset)
        return DiscoveryQuery(
            self.name,
            f"""
            WITH edges AS (
            SELECT e.id AS edge_id, e.valid_from, e.valid_to
            FROM memory_edge AS e
            {_LIVE_ENDPOINTS}
            WHERE e.workspace_id = %(workspace_id)s
              AND e.deleted_at IS NULL
              AND {_NOT_EXCLUDED_EDGE}
              AND (
                  e.valid_from BETWEEN %(now)s - %(window)s AND %(now)s + %(window)s
                  OR e.valid_to BETWEEN %(now)s - %(window)s AND %(now)s + %(window)s
              )
            ORDER BY
                LEAST(
                    abs(extract(epoch FROM e.valid_from - %(now)s)),
                    abs(extract(epoch FROM e.valid_to - %(now)s))
                ),
                e.id
            LIMIT %(limit)s
            ),
            selected AS (
                SELECT edges.edge_id, boundary.verb, boundary.at AS boundary
                FROM edges
                CROSS JOIN LATERAL (
                    SELECT candidate.verb, candidate.at
                    FROM (VALUES
                        ('start'::text, edges.valid_from),
                        ('end'::text, edges.valid_to)
                    ) AS candidate(verb, at)
                    WHERE candidate.at IS NOT NULL
                      AND abs(extract(epoch FROM candidate.at - %(now)s))
                          <= extract(epoch FROM %(window)s::interval)
                    ORDER BY abs(extract(epoch FROM candidate.at - %(now)s))
                    LIMIT 1
                ) AS boundary
            )
            SELECT
                'edge'::text AS anchor_kind,
                selected.edge_id AS anchor_id,
                GREATEST(
                    1 - abs(extract(epoch FROM selected.boundary - %(now)s))
                        / extract(epoch FROM %(window)s::interval),
                    0
                )::double precision AS score,
                selected.verb
                    || CASE
                        WHEN selected.boundary > %(now)s THEN 's '
                        ELSE 'ed '
                    END
                    || ({relative}) AS note,
                NULL::date AS anchor_date
            FROM selected
            """,
            _params(ctx, limit, window=self.window),
        )


@dataclass(frozen=True, slots=True)
class RecentEdges:
    """Edges an ingestion created or added to recently.

    Edges rather than nodes: a new fact about an old node is usually more
    interesting than a new node, and every new node arrives with an edge.
    """

    name: str = "recent"
    window: timedelta = timedelta(days=7)
    half_life: timedelta = timedelta(days=2)

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        offset = "(recent.changed_at AT TIME ZONE %(tz)s)::date - %(today)s"
        relative = _relative_days_sql(offset)
        return DiscoveryQuery(
            self.name,
            f"""
            WITH recent AS (
            SELECT
                e.id AS edge_id,
                max(c.created_at) AS changed_at,
                bool_or(c.change_kind = 'created') AS created,
                e.strength,
                e.confidence
            FROM ingestion_run_graph_change AS c
            JOIN memory_edge AS e
              ON e.workspace_id = c.workspace_id
             AND e.id = c.object_id
             AND e.deleted_at IS NULL
            {_LIVE_ENDPOINTS}
            WHERE c.workspace_id = %(workspace_id)s
              AND c.object_type = 'edge'
              AND c.change_kind IN ('created', 'updated')
              AND c.created_at >= %(now)s - %(window)s
              AND {_NOT_EXCLUDED_EDGE}
            GROUP BY e.workspace_id, e.id
            ORDER BY changed_at DESC, e.id
            LIMIT %(limit)s
            )
            SELECT
                'edge'::text AS anchor_kind,
                recent.edge_id AS anchor_id,
                (
                    power(
                        0.5,
                        GREATEST(
                            extract(epoch FROM %(now)s - recent.changed_at),
                            0
                        ) / extract(epoch FROM %(half_life)s::interval)
                    )
                    * (0.5 + 0.5 * recent.strength * recent.confidence)
                )::double precision AS score,
                CASE WHEN recent.created THEN 'added ' ELSE 'updated ' END
                    || ({relative}) AS note,
                NULL::date AS anchor_date
            FROM recent
            """,
            _params(ctx, limit, window=self.window, half_life=self.half_life),
        )


@dataclass(frozen=True, slots=True)
class RandomEdges:
    """A weighted random draw of facts, favoring strong, evidenced edges."""

    name: str = "random_edge"
    # 0 draws uniformly; 1 draws in proportion to the weight; above 1 sharpens.
    bias: float = 1.0
    # related_to and possibly_same_as carry meaning only in their details.
    vague_relation_weight: float = 0.3
    no_evidence_weight: float = 0.5

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        return DiscoveryQuery(
            self.name,
            f"""
            WITH sampled AS (
            SELECT e.id AS edge_id, x.weight
            FROM memory_edge AS e
            JOIN relation_type AS r ON r.id = e.relation_type_id
            {_LIVE_ENDPOINTS}
            CROSS JOIN LATERAL (
                SELECT power(
                    GREATEST(
                        (0.2 + e.strength) * e.confidence
                        * CASE WHEN r.name IN ('related_to', 'possibly_same_as')
                               THEN %(vague_relation_weight)s ELSE 1 END
                        * CASE WHEN {_HAS_EVIDENCE}
                               THEN 1 ELSE %(no_evidence_weight)s END,
                        0.001
                    ),
                    %(bias)s
                ) AS weight
            ) AS x
            WHERE e.workspace_id = %(workspace_id)s
              AND e.deleted_at IS NULL
              AND {_NOT_EXCLUDED_EDGE}
            ORDER BY power(random(), 1.0 / x.weight) DESC
            LIMIT %(limit)s
            )
            SELECT
                'edge'::text AS anchor_kind,
                sampled.edge_id AS anchor_id,
                (
                    sampled.weight / NULLIF(max(sampled.weight) OVER (), 0)
                )::double precision AS score,
                NULL::text AS note,
                NULL::date AS anchor_date
            FROM sampled
            """,
            _params(
                ctx,
                limit,
                bias=self.bias,
                vague_relation_weight=self.vague_relation_weight,
                no_evidence_weight=self.no_evidence_weight,
            ),
        )


@dataclass(frozen=True, slots=True)
class RandomNodes:
    """A weighted random draw of well-connected, important nodes."""

    name: str = "random_node"
    bias: float = 1.0
    # Date nodes make empty cards; tasks come from OpenTasks.
    exclude_types: tuple[str, ...] = ("date", "task")
    min_degree: int = 2
    # Degree beyond this adds no weight, so hubs (the user's own node) do not
    # crowd out everything else.
    degree_cap: int = 12

    def discovery_query(self, ctx: SurfacingContext, limit: int) -> DiscoveryQuery:
        return DiscoveryQuery(
            self.name,
            f"""
            WITH degree AS (
                SELECT node_id, count(*) AS degree
                FROM (
                    SELECT from_node_id AS node_id
                    FROM memory_edge
                    WHERE workspace_id = %(workspace_id)s AND deleted_at IS NULL
                    UNION ALL
                    SELECT to_node_id
                    FROM memory_edge
                    WHERE workspace_id = %(workspace_id)s AND deleted_at IS NULL
                ) AS endpoints
                GROUP BY node_id
            ),
            sampled AS (
            SELECT n.id AS node_id, x.weight
            FROM memory_node AS n
            JOIN degree ON degree.node_id = n.id
            CROSS JOIN LATERAL (
                SELECT power(
                    GREATEST(
                        n.importance * n.confidence
                        * ln(1 + LEAST(degree.degree, %(degree_cap)s)),
                        0.001
                    ),
                    %(bias)s
                ) AS weight
            ) AS x
            WHERE n.workspace_id = %(workspace_id)s
              AND {_LIVE_NODE.format(n="n")}
              AND n.node_type <> ALL(%(exclude_types)s::text[])
              AND degree.degree >= %(min_degree)s
              AND NOT (n.id = ANY(%(exclude_node_ids)s::uuid[]))
            ORDER BY power(random(), 1.0 / x.weight) DESC
            LIMIT %(limit)s
            )
            SELECT
                'node'::text AS anchor_kind,
                sampled.node_id AS anchor_id,
                (
                    sampled.weight / NULLIF(max(sampled.weight) OVER (), 0)
                )::double precision AS score,
                NULL::text AS note,
                NULL::date AS anchor_date
            FROM sampled
            """,
            _params(
                ctx,
                limit,
                bias=self.bias,
                exclude_types=list(self.exclude_types),
                min_degree=self.min_degree,
                degree_cap=self.degree_cap,
            ),
        )


DEFAULT_UPCOMING_SOURCES: tuple[SurfacingSource, ...] = (
    OpenTasks(),
    UpcomingEvents(),
)

DEFAULT_LANDING_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(OnThisDay(), importance=0.9, limit=2),
    SourceConfig(EdgeValidityChanges(), importance=0.75, limit=1),
    SourceConfig(SourceAnniversaries(), importance=0.7, limit=1),
    SourceConfig(RecentEdges(), importance=0.6, limit=2),
    SourceConfig(RandomEdges(), importance=0.4, limit=2, pool_size=12),
    SourceConfig(RandomNodes(), importance=0.3, limit=1, pool_size=8),
)


# --- Store and subgraph expansion --------------------------------------------


async def _expand_upcoming(
    conn: Any,
    ctx: SurfacingContext,
    batches: Sequence[Sequence[SurfaceAnchor]],
    *,
    limit: int,
    adjacent_edges: int,
) -> list[SurfacedMemory]:
    anchors = [anchor for batch in batches for anchor in batch]
    anchors.sort(
        key=lambda anchor: (
            anchor.when is None,
            anchor.when or date.max,
            -anchor.score,
        )
    )

    upcoming = []
    for anchor in anchors[:limit]:
        edges = await _context_edges(
            conn,
            ctx,
            [anchor.id],
            exclude_edge_ids=[],
            limit=adjacent_edges,
        )
        nodes = await _load_nodes(conn, ctx, {anchor.id} | _endpoints(edges))
        nodes.sort(key=lambda node: (node.node_id != anchor.id, node.title.casefold()))
        upcoming.append(SurfacedMemory(anchor, True, nodes, edges))
    return upcoming


async def _expand_ranked(
    conn: Any,
    ctx: SurfacingContext,
    sources: Sequence[SourceConfig],
    batches: Sequence[Sequence[SurfaceAnchor]],
    *,
    max_cards: int,
    expansion: ExpansionConfig,
    rng: random.Random | None,
    shown_node_ids: set[UUID] | None = None,
    shown_edge_ids: set[UUID] | None = None,
) -> list[SurfacedMemory]:
    ranked = rank_anchors(list(zip(sources, batches)), rng=rng)
    memories: list[SurfacedMemory] = []
    shown_node_ids = set(shown_node_ids or ())
    shown_edge_ids = set(shown_edge_ids or ())
    taken_by_source: dict[int, int] = {}
    unpinned = 0
    for item in ranked:
        if not item.pinned:
            if unpinned >= max_cards:
                break
            limit = sources[item.source_index].limit
            if taken_by_source.get(item.source_index, 0) >= limit:
                continue
            # Sources often land on the same subgraph; the highest-ranked card
            # owns an anchor already shown by upcoming or an earlier memory.
            shown = shown_edge_ids if item.anchor.kind == "edge" else shown_node_ids
            if item.anchor.id in shown:
                continue
        memory = await _expand(conn, ctx, item, expansion, shown_edge_ids)
        if memory is None:
            continue
        memories.append(memory)
        shown_node_ids |= {node.node_id for node in memory.nodes}
        shown_edge_ids |= {edge.edge_id for edge in memory.edges}
        if not item.pinned:
            taken_by_source[item.source_index] = (
                taken_by_source.get(item.source_index, 0) + 1
            )
            unpinned += 1
    return memories


class PostgresMemorySurfacingStore:
    async def surface_landing(
        self,
        *,
        ctx: SurfacingContext,
        max_upcoming: int = 8,
        max_cards: int = 6,
        upcoming_sources: Sequence[SurfacingSource] = DEFAULT_UPCOMING_SOURCES,
        memory_sources: Sequence[SourceConfig] = DEFAULT_LANDING_SOURCES,
        adjacent_edges: int = 4,
        expansion: ExpansionConfig = ExpansionConfig(),
        rng: random.Random | None = None,
    ) -> tuple[list[SurfacedMemory], list[SurfacedMemory]]:
        """Discover every landing source in one query, then expand both sections."""

        upcoming_queries = [
            source.discovery_query(ctx, max_upcoming) for source in upcoming_sources
        ]
        memory_queries = [
            config.source.discovery_query(ctx, config.fetch_limit)
            for config in memory_sources
        ]
        async with get_async_connection() as conn:
            batches = await _fetch_discovery_batches(
                conn, [*upcoming_queries, *memory_queries]
            )
            split = len(upcoming_queries)
            upcoming = await _expand_upcoming(
                conn,
                ctx,
                batches[:split],
                limit=max_upcoming,
                adjacent_edges=adjacent_edges,
            )
            upcoming_node_ids = {item.anchor.id for item in upcoming}
            upcoming_edge_ids = {
                edge.edge_id for item in upcoming for edge in item.edges
            }
            memories = await _expand_ranked(
                conn,
                ctx,
                memory_sources,
                batches[split:],
                max_cards=max_cards,
                expansion=expansion,
                rng=rng,
                shown_node_ids=upcoming_node_ids,
                shown_edge_ids=upcoming_edge_ids,
            )
        return upcoming, memories

    async def surface_upcoming(
        self,
        *,
        ctx: SurfacingContext,
        limit: int = 8,
        sources: Sequence[SurfacingSource] = DEFAULT_UPCOMING_SOURCES,
        adjacent_edges: int = 4,
    ) -> list[SurfacedMemory]:
        """Unfinished tasks and upcoming events with their adjacent edges and nodes.

        Dated items come first by date (so overdue tasks lead), then undated
        tasks by score.
        """

        async with get_async_connection() as conn:
            batches = await _fetch_discovery_batches(
                conn, [source.discovery_query(ctx, limit) for source in sources]
            )
            return await _expand_upcoming(
                conn,
                ctx,
                batches,
                limit=limit,
                adjacent_edges=adjacent_edges,
            )

    async def surface_memories(
        self,
        *,
        ctx: SurfacingContext,
        sources: Sequence[SourceConfig] = DEFAULT_LANDING_SOURCES,
        max_cards: int = 6,
        expansion: ExpansionConfig = ExpansionConfig(),
        rng: random.Random | None = None,
    ) -> list[SurfacedMemory]:
        async with get_async_connection() as conn:
            batches = await _fetch_discovery_batches(
                conn,
                [
                    config.source.discovery_query(ctx, config.fetch_limit)
                    for config in sources
                ],
            )
            return await _expand_ranked(
                conn,
                ctx,
                sources,
                batches,
                max_cards=max_cards,
                expansion=expansion,
                rng=rng,
            )


async def _expand(
    conn: Any,
    ctx: SurfacingContext,
    item: RankedAnchor,
    expansion: ExpansionConfig,
    shown_edge_ids: set[UUID],
) -> SurfacedMemory | None:
    """Build the card subgraph: anchor, context edges, related edges, evidence."""

    anchor = item.anchor
    if anchor.kind == "edge":
        edges = await _load_edges(conn, ctx, [anchor.id])
        if not edges:
            return None
        focus_ids = [edges[0].from_node_id, edges[0].to_node_id]
    else:
        edges = []
        focus_ids = [anchor.id]

    edges += await _context_edges(
        conn,
        ctx,
        focus_ids,
        # Context already shown on an earlier card is not repeated.
        exclude_edge_ids=[edge.edge_id for edge in edges] + list(shown_edge_ids),
        limit=expansion.context_edges,
    )
    node_ids = set(focus_ids) | _endpoints(edges)

    if ctx.embedding_model and expansion.related_edges > 0:
        related = await _related_edges(
            conn, ctx, anchor, node_ids, shown_edge_ids, expansion
        )
        edges += related
        node_ids |= _endpoints(related)

    evidence = await _best_evidence(conn, ctx, [edge.edge_id for edge in edges])
    edges = [replace(edge, evidence=evidence.get(edge.edge_id)) for edge in edges]

    nodes = await _load_nodes(conn, ctx, node_ids)
    if anchor.kind == "node" and all(node.node_id != anchor.id for node in nodes):
        return None
    focus_order = {node_id: index for index, node_id in enumerate(focus_ids)}
    nodes.sort(
        key=lambda node: (
            focus_order.get(node.node_id, len(focus_order)),
            node.title.casefold(),
        )
    )
    return SurfacedMemory(anchor, item.pinned, nodes, edges)


async def _load_edges(
    conn: Any, ctx: SurfacingContext, edge_ids: Sequence[UUID]
) -> list[SurfacedEdge]:
    cursor = await conn.execute(
        f"""
        SELECT {_EDGE_COLUMNS}
        FROM memory_edge AS e
        JOIN relation_type AS r ON r.id = e.relation_type_id
        {_LIVE_ENDPOINTS}
        WHERE e.workspace_id = %(workspace_id)s
          AND e.id = ANY(%(edge_ids)s::uuid[])
          AND e.deleted_at IS NULL
        """,
        {"workspace_id": ctx.workspace_id, "edge_ids": list(edge_ids)},
    )
    return [_edge_from_row(row, "anchor") for row in await cursor.fetchall()]


async def _context_edges(
    conn: Any,
    ctx: SurfacingContext,
    focus_ids: Sequence[UUID],
    *,
    exclude_edge_ids: Sequence[UUID],
    limit: int,
) -> list[SurfacedEdge]:
    """Strongest edges touching the focus nodes.

    Ranked by strength × confidence, preferring edges with evidence, and divided
    by the log degree of the focus endpoint: for an edge anchor like
    "Jessica knows Sarah", context comes mostly from Sarah, not from the hub.
    """

    if limit <= 0:
        return []
    cursor = await conn.execute(
        f"""
        WITH focus_degree AS (
            SELECT
                focus.node_id,
                (
                    SELECT count(*)
                    FROM memory_edge AS d
                    WHERE d.workspace_id = %(workspace_id)s
                      AND d.deleted_at IS NULL
                      AND focus.node_id IN (d.from_node_id, d.to_node_id)
                ) AS degree
            FROM unnest(%(focus_ids)s::uuid[]) AS focus(node_id)
        )
        SELECT {_EDGE_COLUMNS}
        FROM memory_edge AS e
        JOIN relation_type AS r ON r.id = e.relation_type_id
        {_LIVE_ENDPOINTS}
        JOIN focus_degree AS fd ON fd.node_id IN (e.from_node_id, e.to_node_id)
        WHERE e.workspace_id = %(workspace_id)s
          AND e.deleted_at IS NULL
          AND NOT (e.id = ANY(%(exclude_edge_ids)s::uuid[]))
        ORDER BY
            (0.2 + e.strength) * e.confidence
            * CASE WHEN {_HAS_EVIDENCE} THEN 1 ELSE 0.5 END
            / ln(2 + fd.degree) DESC,
            e.id
        LIMIT %(fetch)s
        """,
        {
            "workspace_id": ctx.workspace_id,
            "focus_ids": list(focus_ids),
            "exclude_edge_ids": list(exclude_edge_ids),
            # An edge between two focus nodes is returned once per endpoint.
            "fetch": limit * 2,
        },
    )
    edges: dict[UUID, SurfacedEdge] = {}
    for row in await cursor.fetchall():
        edges.setdefault(row["edge_id"], _edge_from_row(row, "context"))
    return list(edges.values())[:limit]


async def _related_edges(
    conn: Any,
    ctx: SurfacingContext,
    anchor: SurfaceAnchor,
    node_ids: set[UUID],
    shown_edge_ids: set[UUID],
    expansion: ExpansionConfig,
) -> list[SurfacedEdge]:
    """Edges elsewhere in the graph semantically close to the anchor.

    An edge anchor uses its edge projection; a node anchor uses its profile
    projection, which shares the embedding space. Edges touching the card's
    nodes are excluded so related edges add something the card does not show.
    """

    if anchor.kind == "edge":
        cursor = await conn.execute(
            """
            SELECT embedding::text AS vector
            FROM edge_search_projection
            WHERE workspace_id = %(workspace_id)s
              AND edge_id = %(id)s
              AND embedding_model = %(model)s
              AND projection_version = %(version)s
            """,
            {
                "workspace_id": ctx.workspace_id,
                "id": anchor.id,
                "model": ctx.embedding_model,
                "version": EDGE_PROJECTION_VERSION,
            },
        )
    else:
        cursor = await conn.execute(
            """
            SELECT embedding::text AS vector
            FROM node_search_projection
            WHERE workspace_id = %(workspace_id)s
              AND node_id = %(id)s
              AND projection_type = 'profile'
              AND embedding_model = %(model)s
              AND projection_version = %(version)s
            """,
            {
                "workspace_id": ctx.workspace_id,
                "id": anchor.id,
                "model": ctx.embedding_model,
                "version": NODE_PROJECTION_VERSION,
            },
        )
    source = await cursor.fetchone()
    if source is None:
        return []

    cursor = await conn.execute(
        f"""
        WITH nearest AS (
            SELECT
                p.workspace_id,
                p.edge_id,
                1 - (p.embedding <=> %(vector)s::vector) AS similarity
            FROM edge_search_projection AS p
            WHERE p.workspace_id = %(workspace_id)s
              AND p.embedding_model = %(model)s
              AND p.projection_version = %(version)s
            ORDER BY p.embedding <=> %(vector)s::vector
            LIMIT %(fetch)s
        )
        SELECT {_EDGE_COLUMNS}, nearest.similarity
        FROM nearest
        JOIN memory_edge AS e
          ON e.workspace_id = nearest.workspace_id
         AND e.id = nearest.edge_id
         AND e.deleted_at IS NULL
        JOIN relation_type AS r ON r.id = e.relation_type_id
        {_LIVE_ENDPOINTS}
        WHERE nearest.similarity >= %(min_similarity)s
          AND e.from_node_id <> ALL(%(node_ids)s::uuid[])
          AND e.to_node_id <> ALL(%(node_ids)s::uuid[])
          AND NOT (e.id = ANY(%(shown_edge_ids)s::uuid[]))
        ORDER BY nearest.similarity DESC, e.id
        LIMIT %(limit)s
        """,
        {
            "workspace_id": ctx.workspace_id,
            "vector": source["vector"],
            "model": ctx.embedding_model,
            "version": EDGE_PROJECTION_VERSION,
            # Nearest edges mostly sit on the card's own nodes; overfetch so
            # some remain after excluding them.
            "fetch": max(expansion.related_edges * 10, 20),
            "min_similarity": expansion.related_min_similarity,
            "node_ids": list(node_ids),
            "shown_edge_ids": list(shown_edge_ids),
            "limit": expansion.related_edges,
        },
    )
    return [
        _edge_from_row(row, "related", similarity=row["similarity"])
        for row in await cursor.fetchall()
    ]


async def _best_evidence(
    conn: Any, ctx: SurfacingContext, edge_ids: Sequence[UUID]
) -> dict[UUID, SurfacedEvidence]:
    """One quote per edge: highest confidence, verbatim first, then newest."""

    if not edge_ids:
        return {}
    cursor = await conn.execute(
        """
        SELECT DISTINCT ON (ev.edge_id)
            ev.edge_id,
            ev.id AS evidence_id,
            ev.raw_content,
            ev.is_verbatim,
            s.source_type,
            s.created_at AS source_created_at,
            COALESCE(
                (
                    SELECT jsonb_agg(
                        jsonb_build_array(a."start", a.length, a.node_id)
                        ORDER BY a."start", a.length
                    )
                    FROM memory_evidence_annotation AS a
                    WHERE a.workspace_id = ev.workspace_id
                      AND a.evidence_id = ev.id
                ),
                '[]'::jsonb
            ) AS annotations
        FROM memory_evidence AS ev
        JOIN memory_source AS s
          ON s.workspace_id = ev.workspace_id AND s.id = ev.source_id
        WHERE ev.workspace_id = %(workspace_id)s
          AND ev.edge_id = ANY(%(edge_ids)s::uuid[])
        ORDER BY
            ev.edge_id,
            ev.confidence DESC,
            ev.is_verbatim DESC,
            ev.created_at DESC,
            ev.id
        """,
        {"workspace_id": ctx.workspace_id, "edge_ids": list(edge_ids)},
    )
    return {
        row["edge_id"]: SurfacedEvidence(
            evidence_id=row["evidence_id"],
            raw_content=row["raw_content"],
            is_verbatim=row["is_verbatim"],
            source_type=row["source_type"],
            source_created_at=row["source_created_at"],
            annotations=[
                (start, length, UUID(node_id))
                for start, length, node_id in row["annotations"]
            ],
        )
        for row in await cursor.fetchall()
    }


async def _load_nodes(
    conn: Any, ctx: SurfacingContext, node_ids: Iterable[UUID]
) -> list[SurfacedNode]:
    cursor = await conn.execute(
        f"""
        SELECT n.id, n.node_type, n.name, n.summary, n.importance, n.type_specific
        FROM memory_node AS n
        WHERE n.workspace_id = %(workspace_id)s
          AND n.id = ANY(%(node_ids)s::uuid[])
          AND {_LIVE_NODE.format(n="n")}
        """,
        {"workspace_id": ctx.workspace_id, "node_ids": list(node_ids)},
    )
    return [
        SurfacedNode(
            node_id=row["id"],
            node_type=row["node_type"],
            title=row["name"],
            summary=row["summary"],
            importance=row["importance"],
            type_specific=row["type_specific"],
        )
        for row in await cursor.fetchall()
    ]


def _edge_from_row(
    row: dict[str, Any], role: EdgeRole, *, similarity: float | None = None
) -> SurfacedEdge:
    return SurfacedEdge(
        edge_id=row["edge_id"],
        from_node_id=row["from_node_id"],
        to_node_id=row["to_node_id"],
        relation_type=row["relation_type"],
        connector=row["connector"],
        details=row["details"],
        strength=row["strength"],
        confidence=row["confidence"],
        status=row["status"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        role=role,
        similarity=similarity,
    )


def _endpoints(edges: Iterable[SurfacedEdge]) -> set[UUID]:
    return {node_id for edge in edges for node_id in (edge.from_node_id, edge.to_node_id)}
