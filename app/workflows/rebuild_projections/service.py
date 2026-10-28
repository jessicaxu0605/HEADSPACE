"""Drain ``projection_rebuild_queue`` into node and edge search projections.

Run manually until a background worker owns this:

    python -m app.workflows.rebuild_projections
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.db.postgres import close_async_pool, open_async_pool
from app.integrations.ollama.embeddings import OllamaEmbeddingEngine
from app.workflows.rebuild_projections.edge import EdgeProjectionEngine
from app.workflows.rebuild_projections.node import NodeProjectionEngine
from app.workflows.rebuild_projections.postgres import (
    PostgresEdgeProjectionStore,
    PostgresNodeProjectionStore,
    PostgresProjectionQueue,
    QueueObjectType,
)


logger = logging.getLogger(__name__)

Rebuild = Callable[[UUID, UUID], Awaitable[object]]


async def drain_projection_queue(
    *,
    queue: PostgresProjectionQueue,
    object_type: QueueObjectType,
    rebuild: Rebuild,
    batch_size: int = 50,
) -> tuple[int, int]:
    """Process pending rebuilds of one object type; return (done, failed)."""

    done = failed = 0
    while claimed := await queue.claim(object_type=object_type, limit=batch_size):
        for queue_id, workspace_id, object_id in claimed:
            try:
                await rebuild(workspace_id, object_id)
            except Exception:
                logger.exception("%s projection rebuild failed for %s", object_type, object_id)
                failed += 1
                succeeded = False
            else:
                done += 1
                succeeded = True
            await queue.finish(
                workspace_id=workspace_id, queue_id=queue_id, succeeded=succeeded
            )
    return done, failed


async def main() -> None:
    await open_async_pool()
    try:
        queue = PostgresProjectionQueue()
        embedder = OllamaEmbeddingEngine()
        nodes = NodeProjectionEngine(store=PostgresNodeProjectionStore(), embedder=embedder)
        edges = EdgeProjectionEngine(store=PostgresEdgeProjectionStore(), embedder=embedder)
        node_counts = await drain_projection_queue(
            queue=queue,
            object_type="node",
            rebuild=lambda workspace_id, node_id: nodes.rebuild_node(
                workspace_id=workspace_id, node_id=node_id
            ),
        )
        edge_counts = await drain_projection_queue(
            queue=queue,
            object_type="edge",
            rebuild=lambda workspace_id, edge_id: edges.rebuild_edge(
                workspace_id=workspace_id, edge_id=edge_id
            ),
        )
        print(
            f"Nodes: {node_counts[0]} rebuilt, {node_counts[1]} failed. "
            f"Edges: {edge_counts[0]} rebuilt, {edge_counts[1]} failed."
        )
    finally:
        await close_async_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
