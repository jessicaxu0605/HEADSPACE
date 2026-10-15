"""Fill type-specific node details in a second pass over the source.

Runs after proposed-subgraph extraction and before create-or-resolve, so that
identity fields (dates, contact details) are available to resolution. Keeping it
separate from extraction leaves the extraction prompt focused on graph shape.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Protocol

from app.workflows.ingest.type_specific import (
    has_type_specific_fields,
    normalize_type_specific,
    type_specific_external_ids,
)
from app.workflows.ingest.types import (
    ExtractionContext,
    PreparedSource,
    ProposedGraph,
    ProposedNode,
    TypeSpecificEnrichmentInput,
    TypeSpecificEnrichmentNode,
    TypeSpecificPatch,
)


class TypeSpecificEnrichmentLLM(Protocol):
    """LLM adapter that owns the enrichment prompt and output parsing."""

    async def enrich_chunk(
        self,
        *,
        enrichment_input: TypeSpecificEnrichmentInput,
    ) -> list[TypeSpecificPatch]: ...


async def enrich_type_specific(
    prepared_source: PreparedSource,
    proposed_graph: ProposedGraph,
    llm: TypeSpecificEnrichmentLLM,
    *,
    extraction_context: ExtractionContext | None = None,
    reference_date: date | None = None,
) -> None:
    """Update ``type_specific`` and ``summary`` in place on proposed nodes.

    Every node type can get a summary; only types with a field catalog get
    type-specific fields. Identity fields (email, phone, Instagram) are added to
    ``external_ids`` instead of ``type_specific``. Each chunk is sent once, with
    only the nodes it mentions. Values from later chunks replace earlier ones,
    matching how extraction merges repeated nodes.
    """

    nodes = proposed_graph.nodes
    if not nodes:
        return
    nodes_by_id = {node.proposed_node_id: node for node in nodes}
    evidence_by_node: dict[str, list[str]] = defaultdict(list)
    for edge in proposed_graph.edges:
        for evidence in edge.evidence:
            evidence_by_node[edge.from_proposed_node_id].append(evidence.raw_content)
            evidence_by_node[edge.to_proposed_node_id].append(evidence.raw_content)

    reference = (reference_date or date.today()).isoformat()
    temporal_anchor = (
        extraction_context.temporal_anchor if extraction_context is not None else None
    )

    async for chunk in prepared_source.chunks:
        mentioned = [
            node
            for node in nodes
            if _mentioned_in(node, chunk.text, evidence_by_node[node.proposed_node_id])
        ]
        if not mentioned:
            continue
        patches = await llm.enrich_chunk(
            enrichment_input=TypeSpecificEnrichmentInput(
                current_chunk=chunk,
                source_context=prepared_source.source_context,
                nodes=[
                    TypeSpecificEnrichmentNode(
                        proposed_node_id=node.proposed_node_id,
                        node_type=node.node_type,
                        names=list(
                            dict.fromkeys([node.primary_reference_name, *node.names])
                        ),
                        summary=node.summary,
                        type_specific=normalize_type_specific(
                            node.node_type, node.type_specific
                        ),
                    )
                    for node in mentioned
                ],
                reference_date=reference,
                temporal_anchor=temporal_anchor,
            )
        )
        for patch in patches:
            node = nodes_by_id.get(patch.proposed_node_id)
            if node is None:
                continue
            summary = " ".join((patch.summary or "").split())
            if summary:
                node.summary = summary
            if not has_type_specific_fields(node.node_type):
                continue
            for external_id in type_specific_external_ids(
                node.node_type, patch.values
            ):
                if external_id not in node.external_ids:
                    node.external_ids.append(external_id)
            values = normalize_type_specific(node.node_type, patch.values)
            if not values:
                continue
            existing = normalize_type_specific(node.node_type, node.type_specific)
            node.type_specific = {**(existing or {}), **values}


def _mentioned_in(node: ProposedNode, text: str, evidence: list[str]) -> bool:
    folded = text.casefold()
    names = [node.primary_reference_name, *node.names]
    return any(name.strip() and name.casefold() in folded for name in names) or any(
        item in text for item in evidence
    )
