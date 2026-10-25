from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from app.graph.queries.memory_surfacing import (
    DiscoveryQuery,
    SourceConfig,
    SurfaceAnchor,
    SurfacingContext,
    rank_anchors,
)
from app.persistence.postgres.memory_surfacing import (
    DEFAULT_LANDING_SOURCES,
    DEFAULT_UPCOMING_SOURCES,
    _fetch_discovery_batches,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((sql, params))
        return _Cursor(self.rows)


class DiscoveryUnionTests(unittest.IsolatedAsyncioTestCase):
    async def test_combines_namespaces_and_redistributes_source_rows(self) -> None:
        first_id, second_id = uuid4(), uuid4()
        conn = _Connection(
            [
                {
                    "source_index": 1,
                    "anchor_kind": "edge",
                    "anchor_id": second_id,
                    "score": 0.8,
                    "note": "second",
                    "anchor_date": None,
                },
                {
                    "source_index": 0,
                    "anchor_kind": "node",
                    "anchor_id": first_id,
                    "score": 0.4,
                    "note": "first",
                    "anchor_date": None,
                },
            ]
        )
        queries = [
            DiscoveryQuery(
                "first", "SELECT %(limit)s AS value", {"limit": 3}
            ),
            DiscoveryQuery(
                "second", "SELECT %(limit)s AS value", {"limit": 7}
            ),
        ]

        batches = await _fetch_discovery_batches(conn, queries)

        self.assertEqual(len(conn.calls), 1)
        sql, params = conn.calls[0]
        self.assertIn("UNION ALL", sql)
        self.assertIn("%(source_0_limit)s", sql)
        self.assertIn("%(source_1_limit)s", sql)
        self.assertEqual(params, {"source_0_limit": 3, "source_1_limit": 7})
        self.assertEqual([item.id for item in batches[0]], [first_id])
        self.assertEqual([item.id for item in batches[1]], [second_id])
        self.assertEqual(batches[0][0].reason, "first")
        self.assertEqual(batches[1][0].reason, "second")

    async def test_empty_query_list_avoids_database_call(self) -> None:
        conn = _Connection([])

        batches = await _fetch_discovery_batches(conn, [])

        self.assertEqual(batches, [])
        self.assertEqual(conn.calls, [])


class BuiltInDiscoveryQueryTests(unittest.TestCase):
    def test_every_source_returns_the_normalized_anchor_shape(self) -> None:
        ctx = SurfacingContext(workspace_id=uuid4(), now=datetime.now(UTC))
        sources = [
            *DEFAULT_UPCOMING_SOURCES,
            *(config.source for config in DEFAULT_LANDING_SOURCES),
        ]

        for source in sources:
            with self.subTest(source=source.name):
                query = source.discovery_query(ctx, 5)
                self.assertEqual(query.source_name, source.name)
                self.assertIn("anchor_kind", query.sql)
                self.assertIn("anchor_id", query.sql)
                self.assertIn("score", query.sql)
                self.assertIn("note", query.sql)
                self.assertIn("anchor_date", query.sql)

    def test_default_landing_has_eight_swappable_sources(self) -> None:
        self.assertEqual(len(DEFAULT_UPCOMING_SOURCES), 2)
        self.assertEqual(len(DEFAULT_LANDING_SOURCES), 6)
        self.assertEqual(
            len(
                {
                    source.name
                    for source in [
                        *DEFAULT_UPCOMING_SOURCES,
                        *(config.source for config in DEFAULT_LANDING_SOURCES),
                    ]
                }
            ),
            8,
        )


class _ConfiguredSource:
    def __init__(self, name):
        self.name = name

    def discovery_query(self, ctx, limit):
        raise AssertionError("ranking does not execute discovery queries")


class RankingTests(unittest.TestCase):
    def test_duplicate_anchor_belongs_to_highest_importance_source(self) -> None:
        anchor_id = uuid4()
        lower = SourceConfig(_ConfiguredSource("lower"), importance=0.2, limit=1)
        higher = SourceConfig(_ConfiguredSource("higher"), importance=0.8, limit=1)

        ranked = rank_anchors(
            [
                (lower, [SurfaceAnchor("node", anchor_id, "lower", 1.0)]),
                (higher, [SurfaceAnchor("node", anchor_id, "higher", 0.5)]),
            ]
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].source_index, 1)
        self.assertEqual(ranked[0].anchor.reason, "higher")


if __name__ == "__main__":
    unittest.main()
