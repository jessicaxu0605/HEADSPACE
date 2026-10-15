"""Merge queued incoming summaries into canonical node summaries.

Ingestion only fills a summary when a node has none. When a resolved node
already has a different summary, apply queues the incoming one in
``node_summary_refresh_queue``; this worker merges them with one LLM call per
batch, outside ingestion. Run manually until a background worker owns this:

    python -m app.workflows.summaries.refresh_summaries
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SummaryRefreshItem:
    queue_id: UUID
    workspace_id: UUID
    node_id: UUID
    incoming_summaries: list[str]


@dataclass(frozen=True, slots=True)
class NodeSummaryState:
    node_type: str
    title: str
    summary: str | None


@dataclass(frozen=True, slots=True)
class SummaryMergeRequest:
    key: str
    node_type: str
    title: str
    current_summary: str | None
    # Oldest first.
    incoming_summaries: list[str]


class SummaryMergeLLM(Protocol):
    async def merge_summaries(
        self, *, requests: list[SummaryMergeRequest]
    ) -> dict[str, str | None]:
        """Return the merged summary per request key; None keeps the current one."""
        ...


class SummaryRefreshStore(Protocol):
    async def claim(self, *, limit: int) -> list[SummaryRefreshItem]: ...

    async def load_nodes(
        self, items: list[SummaryRefreshItem]
    ) -> dict[tuple[UUID, UUID], NodeSummaryState]: ...

    async def save_summary(
        self, *, workspace_id: UUID, node_id: UUID, summary: str
    ) -> None: ...

    async def finish(
        self, *, workspace_id: UUID, queue_id: UUID, succeeded: bool
    ) -> None: ...


async def drain_summary_refresh_queue(
    *,
    store: SummaryRefreshStore,
    llm: SummaryMergeLLM,
    batch_size: int = 20,
) -> tuple[int, int]:
    """Merge every pending refresh; return (done, failed).

    A failed LLM call fails its whole batch. Failed rows keep their incoming
    summaries for a later retry.
    """

    done = failed = 0
    while claimed := await store.claim(limit=batch_size):
        nodes = await store.load_nodes(claimed)
        requests = [
            SummaryMergeRequest(
                key=str(index),
                node_type=node.node_type,
                title=node.title,
                current_summary=node.summary,
                incoming_summaries=item.incoming_summaries,
            )
            for index, item in enumerate(claimed)
            if (node := nodes.get((item.workspace_id, item.node_id))) is not None
        ]
        try:
            merged = await llm.merge_summaries(requests=requests) if requests else {}
        except Exception:
            logger.exception("Summary merge failed for %d node(s)", len(requests))
            merged = None

        for index, item in enumerate(claimed):
            node = nodes.get((item.workspace_id, item.node_id))
            # A node deleted or merged since queueing has nothing left to update;
            # merges re-queue the duplicate's pending summaries on the survivor.
            succeeded = node is None or merged is not None
            if node is not None and merged is not None:
                summary = " ".join((merged.get(str(index)) or "").split())
                if summary and summary != node.summary:
                    try:
                        await store.save_summary(
                            workspace_id=item.workspace_id,
                            node_id=item.node_id,
                            summary=summary,
                        )
                    except Exception:
                        logger.exception("Saving summary failed for %s", item.node_id)
                        succeeded = False
            done += succeeded
            failed += not succeeded
            await store.finish(
                workspace_id=item.workspace_id,
                queue_id=item.queue_id,
                succeeded=succeeded,
            )
    return done, failed


async def main() -> None:
    from app.db import close_async_pool, open_async_pool
    from app.persistence.postgres.summary_refresh_queue import (
        PostgresSummaryRefreshStore,
    )
    from app.workflows.summaries.groq_summary_merge import GroqSummaryMergeLLM

    await open_async_pool()
    try:
        done, failed = await drain_summary_refresh_queue(
            store=PostgresSummaryRefreshStore(),
            llm=GroqSummaryMergeLLM(),
        )
        print(f"Summaries: {done} refreshed, {failed} failed.")
    finally:
        await close_async_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
