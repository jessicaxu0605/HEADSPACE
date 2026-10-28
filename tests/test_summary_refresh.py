from __future__ import annotations

import json
import unittest
from uuid import UUID, uuid4

from app.workflows.ingest.resolution.service import (
    CanonicalNode,
    FillCanonicalNodeSummary,
    QueueCanonicalNodeSummaryRefresh,
    resolve_proposed_subgraph,
)
from app.workflows.ingest.resolution.in_memory import (
    SimpleCanonicalGraphResolverStore,
)
from app.workflows.ingest.models import ProposedGraph, ProposedNode
from app.workflows.summaries.adapters.groq import parse_summary_merge_output
from app.workflows.summaries.refresh.service import (
    NodeSummaryState,
    SummaryMergeRequest,
    SummaryRefreshItem,
    drain_summary_refresh_queue,
)


class FakeSummaryStore:
    def __init__(
        self,
        items: list[SummaryRefreshItem],
        nodes: dict[tuple[UUID, UUID], NodeSummaryState],
    ) -> None:
        self.pending = list(items)
        self.nodes = nodes
        self.saved: dict[UUID, str] = {}
        self.finished: dict[UUID, bool] = {}

    async def claim(self, *, limit: int) -> list[SummaryRefreshItem]:
        claimed, self.pending = self.pending[:limit], self.pending[limit:]
        return claimed

    async def load_nodes(self, items):
        return {
            key: node
            for item in items
            if (node := self.nodes.get(key := (item.workspace_id, item.node_id)))
        }

    async def save_summary(self, *, workspace_id, node_id, summary) -> None:
        self.saved[node_id] = summary

    async def finish(self, *, workspace_id, queue_id, succeeded) -> None:
        self.finished[queue_id] = succeeded


class FakeMergeLLM:
    def __init__(self, merged: dict[str, str | None] | Exception) -> None:
        self.merged = merged
        self.requests: list[list[SummaryMergeRequest]] = []

    async def merge_summaries(self, *, requests):
        self.requests.append(requests)
        if isinstance(self.merged, Exception):
            raise self.merged
        return self.merged


def _item(workspace_id: UUID, node_id: UUID, *summaries: str) -> SummaryRefreshItem:
    return SummaryRefreshItem(uuid4(), workspace_id, node_id, list(summaries))


class DrainSummaryRefreshQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_merges_a_batch_in_one_call_and_saves_changes(self) -> None:
        workspace_id, sg, wat, gone = uuid4(), uuid4(), uuid4(), uuid4()
        items = [
            _item(workspace_id, sg, "Hot and humid.", "Hub for Asian finance."),
            _item(workspace_id, wat, "A tech hub."),
            _item(workspace_id, gone, "Deleted since queueing."),
        ]
        store = FakeSummaryStore(
            items,
            {
                (workspace_id, sg): NodeSummaryState(
                    "location", "Singapore", "A city-state."
                ),
                (workspace_id, wat): NodeSummaryState(
                    "location", "Waterloo", "A city in Ontario."
                ),
            },
        )
        llm = FakeMergeLLM(
            {"0": "A hot, humid city-state and Asian finance hub.", "1": None}
        )

        done, failed = await drain_summary_refresh_queue(store=store, llm=llm)

        self.assertEqual((done, failed), (3, 0))
        [requests] = llm.requests
        self.assertEqual(
            requests[0],
            SummaryMergeRequest(
                "0",
                "location",
                "Singapore",
                "A city-state.",
                ["Hot and humid.", "Hub for Asian finance."],
            ),
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            store.saved, {sg: "A hot, humid city-state and Asian finance hub."}
        )
        self.assertTrue(all(store.finished.values()))

    async def test_llm_failure_fails_the_batch_but_not_inactive_nodes(self) -> None:
        workspace_id, sg, gone = uuid4(), uuid4(), uuid4()
        live, stale = _item(workspace_id, sg, "New."), _item(workspace_id, gone, "x")
        store = FakeSummaryStore(
            [live, stale],
            {(workspace_id, sg): NodeSummaryState("location", "Singapore", "Old.")},
        )

        with self.assertLogs(
            "app.workflows.summaries.refresh.service", "ERROR"
        ):
            done, failed = await drain_summary_refresh_queue(
                store=store, llm=FakeMergeLLM(RuntimeError("rate limited"))
            )

        self.assertEqual((done, failed), (1, 1))
        self.assertEqual(
            store.finished, {live.queue_id: False, stale.queue_id: True}
        )
        self.assertEqual(store.saved, {})


class GroqSummaryMergeParsingTests(unittest.TestCase):
    def test_keeps_requested_keys_and_blank_means_no_change(self) -> None:
        raw = json.dumps(
            {
                "summaries": [
                    {"key": "0", "summary": " Merged. "},
                    {"key": "1", "summary": "  "},
                    {"key": "9", "summary": "Unknown key."},
                ]
            }
        )

        self.assertEqual(
            parse_summary_merge_output(raw, {"0", "1"}), {"0": "Merged.", "1": None}
        )


class ResolveSummaryActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_queues_a_different_summary_for_an_existing_one(self) -> None:
        bare = CanonicalNode(uuid4(), "location", "Singapore")
        same = CanonicalNode(uuid4(), "location", "Toronto", summary="A big city.")
        different = CanonicalNode(
            uuid4(), "location", "Waterloo", summary="A city in Ontario."
        )
        plan = await resolve_proposed_subgraph(
            workspace_id=uuid4(),
            source_id=uuid4(),
            proposed_graph=ProposedGraph(
                nodes=[
                    ProposedNode("a", "location", ["Singapore"], "Singapore", "City."),
                    ProposedNode("b", "location", ["Toronto"], "Toronto", "a big  city."),
                    ProposedNode("c", "location", ["Waterloo"], "Waterloo", "Tech hub."),
                ]
            ),
            store=SimpleCanonicalGraphResolverStore(nodes=[bare, same, different]),
        )

        summary_actions = [
            action
            for action in plan.node_actions
            if isinstance(
                action, (FillCanonicalNodeSummary, QueueCanonicalNodeSummaryRefresh)
            )
        ]
        self.assertEqual(
            summary_actions,
            [
                FillCanonicalNodeSummary(bare.node_id, "City."),
                QueueCanonicalNodeSummaryRefresh(different.node_id, "Tech hub."),
            ],
        )


if __name__ == "__main__":
    unittest.main()
