"""Compare single-call and two-stage extraction on real sources.

Runs extraction only (no resolution, no graph writes) and prints each run's nodes,
edges, and summary stats. Usage:

    venv/bin/python -m scripts.compare_extraction --recent 3 --runs 2
    venv/bin/python -m scripts.compare_extraction path/to/source.txt
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

from app.persistence.postgres.canonical_graph import load_active_relation_types
from app.persistence.postgres.connection import get_connection
from app.storage import read_storage_object
from app.workflows.ingest.extract_proposed_subgraph import extract_proposed_subgraph
from app.workflows.ingest.groq_chunk_extraction import GroqChunkExtractionLLM
from app.workflows.ingest.groq_two_stage_extraction import GroqTwoStageExtractionLLM
from app.workflows.ingest.relation_types import (
    allows_endpoints,
    canonicalize_relation_type,
)
from app.workflows.ingest.types import (
    ExtractionSourceContext,
    PreparedSource,
    SourceChunk,
)


async def _single_chunk(source_id, text):
    yield SourceChunk(f"{source_id}:all", source_id, text)


def _recent_sources(limit: int) -> list[tuple[str, str]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, storage_object_key FROM memory_source
            ORDER BY created_at DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [
        (f"source {row['id']}", read_storage_object(row["storage_object_key"]).decode())
        for row in rows
    ]


class CallCounter:
    def __init__(self, llm) -> None:
        self.calls = 0
        completions = llm._client.chat.completions
        original = completions.create

        async def counted(**kwargs):
            self.calls += 1
            return await original(**kwargs)

        completions.create = counted


async def run_once(mode: str, text: str, relation_definitions) -> str:
    llm = (
        GroqTwoStageExtractionLLM(relation_definitions=relation_definitions)
        if mode == "two_stage"
        else GroqChunkExtractionLLM(relation_definitions=relation_definitions)
    )
    counter = CallCounter(llm)
    source_id = uuid4()
    prepared = PreparedSource(
        source_context=ExtractionSourceContext(source_id, "manual_entry", {}),
        chunks=_single_chunk(source_id, text),
    )
    started = time.monotonic()
    try:
        result = await extract_proposed_subgraph(
            prepared, llm, relation_definitions=relation_definitions
        )
    except Exception as exc:  # report and keep comparing
        return f"  FAILED: {type(exc).__name__}: {exc}\n"
    elapsed = time.monotonic() - started

    graph = result.proposed_graph
    nodes = {node.proposed_node_id: node for node in graph.nodes}
    relation_counts: Counter[str] = Counter()
    invalid_endpoints = 0
    lines = []
    for node in graph.nodes:
        summary = f" — {node.summary}" if node.summary else ""
        lines.append(f"    [{node.node_type}] {node.primary_reference_name}{summary}")
    lines.append("")
    for edge in graph.edges:
        from_node = nodes.get(edge.from_proposed_node_id)
        to_node = nodes.get(edge.to_proposed_node_id)
        resolution = canonicalize_relation_type(edge.relation_type, relation_definitions)
        canonical = resolution[0].name if resolution else edge.relation_type
        relation_counts[canonical] += 1
        flag = ""
        if resolution and from_node and to_node:
            relation, reverse = resolution
            ends = (to_node.node_type, from_node.node_type) if reverse else (
                from_node.node_type,
                to_node.node_type,
            )
            if not allows_endpoints(relation, *ends):
                invalid_endpoints += 1
                flag = "  !! endpoint mismatch"
        evidence = " | ".join(f"“{item.raw_content}”" for item in edge.evidence)
        details = f" ({edge.details})" if edge.details else ""
        lines.append(
            f"    {from_node.primary_reference_name if from_node else edge.from_proposed_node_id}"
            f" -[{edge.relation_type}]-> "
            f"{to_node.primary_reference_name if to_node else edge.to_proposed_node_id}"
            f"{details}{flag}\n        evidence: {evidence}"
        )
    gaps = [
        item.details
        for item in result.repair_items
        if item.item_type == "relation_catalog_gap"
    ]
    if gaps:
        lines.append("\n    catalog gaps:")
        lines.extend(
            f"      {gap['from_type']} -> {gap['to_type']}: chose "
            f"{gap['chosen_relation']}, wanted {gap['preferred_relation']}"
            for gap in gaps
        )
    node_types = Counter(str(node.node_type) for node in graph.nodes)
    stats = (
        f"  {len(graph.nodes)} nodes {dict(node_types)} · {len(graph.edges)} edges · "
        f"related_to {relation_counts['related_to']} · "
        f"endpoint mismatches {invalid_endpoints} · "
        f"{counter.calls} LLM calls · {elapsed:.1f}s"
    )
    return stats + "\n" + "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--recent", type=int, default=0)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--modes", default="single,two_stage", help="comma-separated modes"
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=30,
        help="seconds between runs, to stay under Groq's tokens-per-minute limit",
    )
    args = parser.parse_args()

    sources = [(str(path), path.read_text()) for path in args.files]
    if args.recent:
        sources.extend(_recent_sources(args.recent))
    relation_definitions = load_active_relation_types()

    for label, text in sources:
        print(f"\n{'=' * 80}\n{label}\n{text[:200]!r}…")
        for mode in args.modes.split(","):
            for run in range(args.runs):
                print(f"\n--- {mode} · run {run + 1}", flush=True)
                print(await run_once(mode, text, relation_definitions), flush=True)
                await asyncio.sleep(args.pause)


if __name__ == "__main__":
    asyncio.run(main())
