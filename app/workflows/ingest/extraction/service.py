"""Build a proposed graph by aggregating chunk-local LLM extractions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Protocol

from app.workflows.ingest.edge_helpers import combine_edge_details
from app.workflows.ingest.relation_types import (
    RelationDefinition,
    allows_endpoints,
    canonicalize_relation_type,
    valid_relation_labels,
)
from app.workflows.ingest.models import (
    BoundaryContext,
    ChunkExtractionInput,
    ChunkExtractionOutput,
    ChunkExtractionRepairContext,
    ExtractionContext,
    ExtractionSourceContext,
    ExtractionValidationIssue,
    NODE_TYPES,
    PreparedSource,
    PreviousIngestContext,
    ProposedEdgeHandoff,
    ProposedGraph,
    ProposedGraphHandoff,
    ProposedNodeHandoff,
    ProposedSubgraphExtractionResult,
    SourceChunk,
)


VALID_NODE_TYPES = set(NODE_TYPES)

# Issues worth a repair attempt but not worth failing the ingest over: if they
# survive the last attempt, the output is accepted and resolution repairs or
# downgrades the affected edges.
SOFT_ISSUE_CODES = {"invalid_relation_endpoints"}


class ChunkExtractionLLM(Protocol):
    """LLM adapter that owns the chunk-extraction prompt and output parsing."""

    async def extract_chunk(
        self,
        *,
        extraction_input: ChunkExtractionInput,
        repair_context: ChunkExtractionRepairContext | None = None,
    ) -> ChunkExtractionOutput:
        """Return structured output, repairing raw schema errors internally."""
        ...


@dataclass(frozen=True, slots=True)
class ProposedSubgraphLimits:
    """Temporary safety limits until continuation-on-limit is implemented."""

    max_nodes: int = 200
    max_edges: int = 500


class ProposedSubgraphLimitError(RuntimeError):
    """Raised to fail the job when the accumulated graph reaches its limit."""


class InvalidChunkExtractionOutputError(ValueError):
    """Raised when a chunk-local LLM extraction is internally inconsistent."""

    def __init__(
        self,
        issues: list[ExtractionValidationIssue],
        *,
        invalid_output: ChunkExtractionOutput,
        chunk_id: str,
        attempts: int,
    ) -> None:
        self.issues = issues
        self.invalid_output = invalid_output
        self.chunk_id = chunk_id
        self.attempts = attempts
        details = "; ".join(
            f"{issue.path}: {issue.message}" for issue in issues
        )
        super().__init__(
            f"Chunk {chunk_id!r} remained invalid after {attempts} attempt(s): "
            f"{details}"
        )


def build_initial_extraction_context(
    source_context: ExtractionSourceContext,
) -> ExtractionContext:
    """Seed rolling context from extraction-safe source metadata."""

    metadata = json.dumps(
        source_context.extraction_metadata,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return ExtractionContext(
        source_summary=(
            f"Source type: {source_context.source_type}. "
            f"Source metadata: {metadata}"
        )
    )


def build_chunk_extraction_input(
    source_context: ExtractionSourceContext,
    current_chunk: SourceChunk,
    *,
    previous_output: ChunkExtractionOutput | None = None,
    proposed_graph: ProposedGraph | None = None,
    previous_chunk: SourceChunk | None = None,
    next_chunk: SourceChunk | None = None,
    previous_ingest_context: PreviousIngestContext | None = None,
    boundary_chars: int = 500,
) -> ChunkExtractionInput:
    """Build a model input, carrying forward snapshot state when available."""
    if previous_output is None:
        extraction_context = build_initial_extraction_context(source_context)
    else:
        extraction_context = previous_output.updated_extraction_context
    proposed_graph_handoff = build_proposed_graph_handoff(
        proposed_graph or ProposedGraph()
    )

    boundary_context = _build_boundary_context(
        previous_chunk=previous_chunk,
        next_chunk=next_chunk,
        boundary_chars=boundary_chars,
    )

    return ChunkExtractionInput(
        current_chunk=current_chunk,
        source_context=source_context,
        extraction_context=extraction_context,
        proposed_graph_handoff=proposed_graph_handoff,
        boundary_context=boundary_context,
        previous_ingest_context=previous_ingest_context,
    )


async def extract_proposed_subgraph(
    prepared_source: PreparedSource,
    llm: ChunkExtractionLLM,
    *,
    previous_ingest_context: PreviousIngestContext | None = None,
    relation_definitions: list[RelationDefinition] | None = None,
    limits: ProposedSubgraphLimits = ProposedSubgraphLimits(),
    boundary_chars: int = 500,
    max_repair_attempts: int = 2,
) -> ProposedSubgraphExtractionResult:
    """Extract every chunk and return their accumulated proposed graph.

    Reaching a proposed-subgraph limit currently raises and therefore halts the
    ingest job.  This check is isolated so it can later trigger the documented
    freeze/resolve/continue flow without changing chunk-input construction.
    """

    previous_chunk: SourceChunk | None = None
    previous_output: ChunkExtractionOutput | None = None
    proposed_graph = ProposedGraph()
    repair_items = []

    async for current_chunk, next_chunk in _with_lookahead(prepared_source.chunks):
        extraction_input = build_chunk_extraction_input(
            prepared_source.source_context,
            current_chunk,
            previous_output=previous_output,
            proposed_graph=proposed_graph,
            previous_chunk=previous_chunk,
            next_chunk=next_chunk,
            previous_ingest_context=previous_ingest_context,
            boundary_chars=boundary_chars,
        )

        repair_context: ChunkExtractionRepairContext | None = None
        for attempt in range(max_repair_attempts + 1):
            output = await llm.extract_chunk(
                extraction_input=extraction_input,
                repair_context=repair_context,
            )
            _mark_verbatim_evidence(output.chunk_subgraph, current_chunk.text)
            issues = _collect_chunk_subgraph_issues(
                output.chunk_subgraph,
                accumulated_graph=proposed_graph,
                relation_definitions=relation_definitions or [],
            )
            only_soft_issues_left = attempt == max_repair_attempts and all(
                issue.code in SOFT_ISSUE_CODES for issue in issues
            )

            if not issues or only_soft_issues_left:
                candidate_graph = _merge_chunk_subgraph(
                    proposed_graph,
                    output.chunk_subgraph,
                )
                _halt_on_proposed_subgraph_limit(candidate_graph, limits)
                proposed_graph = candidate_graph
                repair_items.extend(output.repair_items)
                break

            if attempt == max_repair_attempts:
                raise InvalidChunkExtractionOutputError(
                    issues,
                    invalid_output=output,
                    chunk_id=current_chunk.chunk_id,
                    attempts=attempt + 1,
                )

            repair_context = ChunkExtractionRepairContext(
                invalid_output=output,
                issues=issues,
            )

        previous_chunk = current_chunk
        previous_output = output

    if previous_output is not None:
        return ProposedSubgraphExtractionResult(
            proposed_graph=proposed_graph,
            proposed_graph_handoff=build_proposed_graph_handoff(proposed_graph),
            updated_extraction_context=previous_output.updated_extraction_context,
            repair_items=repair_items,
        )

    return ProposedSubgraphExtractionResult(
        proposed_graph=ProposedGraph(),
        proposed_graph_handoff=ProposedGraphHandoff(),
        updated_extraction_context=build_initial_extraction_context(
            prepared_source.source_context
        ),
    )


async def _with_lookahead(
    chunks: AsyncIterable[SourceChunk],
) -> AsyncIterator[tuple[SourceChunk, SourceChunk | None]]:
    iterator = aiter(chunks)
    try:
        current = await anext(iterator)
    except StopAsyncIteration:
        return

    while True:
        try:
            next_chunk = await anext(iterator)
        except StopAsyncIteration:
            yield current, None
            return
        yield current, next_chunk
        current = next_chunk


def _build_boundary_context(
    *,
    previous_chunk: SourceChunk | None,
    next_chunk: SourceChunk | None,
    boundary_chars: int,
) -> BoundaryContext | None:
    if boundary_chars < 0:
        raise ValueError("boundary_chars must be non-negative")
    if boundary_chars == 0 or (previous_chunk is None and next_chunk is None):
        return None

    return BoundaryContext(
        previous_chunk_tail=(
            previous_chunk.text[-boundary_chars:] if previous_chunk is not None else None
        ),
        next_chunk_head=(next_chunk.text[:boundary_chars] if next_chunk is not None else None),
    )


def build_proposed_graph_handoff(proposed_graph: ProposedGraph) -> ProposedGraphHandoff:
    """Derive the cross-chunk identity context from the accumulated graph.

    The model no longer writes the handoff: rewriting every node each chunk used
    about a third of the output budget and was a common source of repairs.
    """

    return ProposedGraphHandoff(
        nodes=[
            ProposedNodeHandoff(
                proposed_node_id=node.proposed_node_id,
                node_type=node.node_type,
                names=list(node.names),
                primary_name=node.primary_reference_name,
                external_ids=list(node.external_ids),
                handoff_description=node.summary,
            )
            for node in proposed_graph.nodes
        ],
        edges=[
            ProposedEdgeHandoff(
                proposed_edge_id=edge.proposed_edge_id,
                from_proposed_node_id=edge.from_proposed_node_id,
                to_proposed_node_id=edge.to_proposed_node_id,
                relation_type=edge.relation_type,
            )
            for edge in proposed_graph.edges
        ],
    )


def _collect_chunk_subgraph_issues(
    chunk_subgraph: ProposedGraph,
    *,
    accumulated_graph: ProposedGraph,
    relation_definitions: list[RelationDefinition] | None = None,
) -> list[ExtractionValidationIssue]:
    issues: list[ExtractionValidationIssue] = []
    node_ids = [node.proposed_node_id for node in chunk_subgraph.nodes]
    if len(node_ids) != len(set(node_ids)):
        issues.append(
            _issue(
                "duplicate_node_id",
                "chunk_subgraph.nodes",
                "Chunk subgraph contains duplicate node IDs.",
            )
        )

    edge_ids = [edge.proposed_edge_id for edge in chunk_subgraph.edges]
    if len(edge_ids) != len(set(edge_ids)):
        issues.append(
            _issue(
                "duplicate_edge_id",
                "chunk_subgraph.edges",
                "Chunk subgraph contains duplicate edge IDs.",
            )
        )

    accumulated_nodes = {
        node.proposed_node_id: node for node in accumulated_graph.nodes
    }
    accumulated_edges = {
        edge.proposed_edge_id: edge for edge in accumulated_graph.edges
    }
    known_node_ids = set(accumulated_nodes) | set(node_ids)
    node_types = {
        **{node_id: node.node_type for node_id, node in accumulated_nodes.items()},
        **{node.proposed_node_id: node.node_type for node in chunk_subgraph.nodes},
    }

    for node_index, node in enumerate(chunk_subgraph.nodes):
        node_path = f"chunk_subgraph.nodes[{node_index}]"
        _collect_nonempty_issue(
            issues,
            node.proposed_node_id,
            code="empty_node_id",
            path=f"{node_path}.proposed_node_id",
            description="Proposed node ID",
        )
        if node.node_type not in VALID_NODE_TYPES:
            issues.append(
                _issue(
                    "unsupported_node_type",
                    f"{node_path}.node_type",
                    f"Node {node.proposed_node_id!r} has unsupported type "
                    f"{node.node_type!r}.",
                )
            )
        _collect_nonempty_issue(
            issues,
            node.primary_reference_name,
            code="empty_primary_name",
            path=f"{node_path}.primary_reference_name",
            description="Primary reference name",
        )
        if not node.names:
            issues.append(
                _issue(
                    "missing_node_names",
                    f"{node_path}.names",
                    f"Node {node.proposed_node_id!r} must have at least one name.",
                )
            )
        for name_index, name in enumerate(node.names):
            _collect_nonempty_issue(
                issues,
                name,
                code="empty_node_name",
                path=f"{node_path}.names[{name_index}]",
                description=f"Node {node.proposed_node_id!r} name",
            )
        if node.primary_reference_name not in node.names:
            issues.append(
                _issue(
                    "primary_name_missing_from_names",
                    f"{node_path}.primary_reference_name",
                    f"Node {node.proposed_node_id!r} primary reference name must "
                    "appear in its names.",
                )
            )
        for external_id_index, external_id in enumerate(node.external_ids):
            external_id_path = f"{node_path}.external_ids[{external_id_index}]"
            _collect_nonempty_issue(
                issues,
                external_id.provider,
                code="empty_external_id_provider",
                path=f"{external_id_path}.provider",
                description=f"Node {node.proposed_node_id!r} external ID provider",
            )
            _collect_nonempty_issue(
                issues,
                external_id.external_id,
                code="empty_external_id_value",
                path=f"{external_id_path}.external_id",
                description=f"Node {node.proposed_node_id!r} external ID value",
            )
        existing_node = accumulated_nodes.get(node.proposed_node_id)
        if existing_node is not None and existing_node.node_type != node.node_type:
            issues.append(
                _issue(
                    "node_type_changed",
                    f"{node_path}.node_type",
                    f"Node {node.proposed_node_id!r} changed type from "
                    f"{existing_node.node_type!r} to {node.node_type!r}; a different "
                    "node type requires a new proposed node ID.",
                )
            )

    for edge_index, edge in enumerate(chunk_subgraph.edges):
        edge_path = f"chunk_subgraph.edges[{edge_index}]"
        _collect_nonempty_issue(
            issues,
            edge.proposed_edge_id,
            code="empty_edge_id",
            path=f"{edge_path}.proposed_edge_id",
            description="Proposed edge ID",
        )
        _collect_nonempty_issue(
            issues,
            edge.relation_type,
            code="empty_relation_type",
            path=f"{edge_path}.relation_type",
            description=f"Edge {edge.proposed_edge_id!r} relation",
        )
        if (
            edge.from_proposed_node_id not in known_node_ids
            or edge.to_proposed_node_id not in known_node_ids
        ):
            issues.append(
                _issue(
                    "missing_edge_endpoint",
                    edge_path,
                    f"Edge {edge.proposed_edge_id!r} refers to a missing proposed node.",
                )
            )
        else:
            endpoint_issue = _relation_endpoint_issue(
                edge.proposed_edge_id,
                edge.relation_type,
                node_types[edge.from_proposed_node_id],
                node_types[edge.to_proposed_node_id],
                relation_definitions or [],
                path=f"{edge_path}.relation_type",
            )
            if endpoint_issue is not None:
                issues.append(endpoint_issue)
        existing_edge = accumulated_edges.get(edge.proposed_edge_id)
        if existing_edge is not None and (
            existing_edge.from_proposed_node_id != edge.from_proposed_node_id
            or existing_edge.to_proposed_node_id != edge.to_proposed_node_id
            or existing_edge.relation_type != edge.relation_type
        ):
            issues.append(
                _issue(
                    "edge_identity_changed",
                    edge_path,
                    f"Edge {edge.proposed_edge_id!r} conflicts with its earlier identity.",
                )
            )
        for evidence_index, evidence in enumerate(edge.evidence):
            evidence_path = f"{edge_path}.evidence[{evidence_index}]"
            if not evidence.raw_content:
                issues.append(
                    _issue(
                        "empty_evidence",
                        f"{evidence_path}.raw_content",
                        f"Edge {edge.proposed_edge_id!r} contains empty evidence.",
                    )
                )
            if not _is_unit_interval(evidence.confidence):
                issues.append(
                    _issue(
                        "invalid_evidence_confidence",
                        f"{evidence_path}.confidence",
                        f"Evidence confidence for edge {edge.proposed_edge_id!r} "
                        "must be between 0 and 1.",
                    )
                )
            if (
                evidence.strength_boost is not None
                and not _is_unit_interval(evidence.strength_boost)
            ):
                issues.append(
                    _issue(
                        "invalid_evidence_strength",
                        f"{evidence_path}.strength_boost",
                        f"Evidence strength boost for edge {edge.proposed_edge_id!r} "
                        "must be between 0 and 1.",
                    )
                )
            for annotation_index, annotation in enumerate(evidence.annotations):
                annotation_path = (
                    f"{evidence_path}.annotations[{annotation_index}]"
                )
                if annotation.start < 0 or annotation.length <= 0:
                    issues.append(
                        _issue(
                            "invalid_annotation_range",
                            annotation_path,
                            f"Evidence annotation for edge {edge.proposed_edge_id!r} "
                            "must have a non-negative start and positive length.",
                        )
                    )
                if annotation.start + annotation.length > len(evidence.raw_content):
                    issues.append(
                        _issue(
                            "annotation_out_of_bounds",
                            annotation_path,
                            f"Evidence annotation for edge {edge.proposed_edge_id!r} "
                            "falls outside its raw content.",
                        )
                    )
                if annotation.local_node_id not in known_node_ids:
                    issues.append(
                        _issue(
                            "missing_annotation_node",
                            f"{annotation_path}.local_node_id",
                            f"Evidence annotation for edge {edge.proposed_edge_id!r} "
                            f"refers to missing node {annotation.local_node_id!r}.",
                        )
                    )

    return issues


def _relation_endpoint_issue(
    edge_id: str,
    relation_type: str,
    from_type: str,
    to_type: str,
    relation_definitions: list[RelationDefinition],
    *,
    path: str,
) -> ExtractionValidationIssue | None:
    """Flag a catalog label used between node types it does not allow."""

    resolution = canonicalize_relation_type(relation_type, relation_definitions)
    if resolution is None:
        return None
    relation, reverse = resolution
    canonical_from, canonical_to = (
        (to_type, from_type) if reverse else (from_type, to_type)
    )
    if allows_endpoints(relation, canonical_from, canonical_to):
        return None
    options = ", ".join(valid_relation_labels(relation_definitions, from_type, to_type))
    return _issue(
        "invalid_relation_endpoints",
        path,
        f"Edge {edge_id!r} uses {relation_type!r}, which cannot connect "
        f"{from_type} -> {to_type}. Choose the most specific relation that is "
        f"valid for {from_type} -> {to_type}: {options}.",
    )


def _collect_nonempty_issue(
    issues: list[ExtractionValidationIssue],
    value: object,
    *,
    code: str,
    path: str,
    description: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(
            _issue(code, path, f"{description} must be non-empty.")
        )


def _mark_verbatim_evidence(chunk_subgraph: ProposedGraph, chunk_text: str) -> None:
    """Flag evidence that is not a span of the chunk instead of rejecting it."""

    normalized_chunk = _normalize_whitespace(chunk_text)
    for edge in chunk_subgraph.edges:
        edge.evidence = [
            replace(
                evidence,
                is_verbatim=_normalize_whitespace(evidence.raw_content)
                in normalized_chunk,
            )
            for evidence in edge.evidence
        ]


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _issue(code: str, path: str, message: str) -> ExtractionValidationIssue:
    return ExtractionValidationIssue(code=code, path=path, message=message)


def _is_unit_interval(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
    )


def _merge_chunk_subgraph(
    accumulated_graph: ProposedGraph,
    chunk_subgraph: ProposedGraph,
) -> ProposedGraph:
    merged_graph = deepcopy(accumulated_graph)
    nodes_by_id = {
        node.proposed_node_id: node for node in merged_graph.nodes
    }
    edges_by_id = {
        edge.proposed_edge_id: edge for edge in merged_graph.edges
    }

    for incoming_node in chunk_subgraph.nodes:
        existing_node = nodes_by_id.get(incoming_node.proposed_node_id)
        if existing_node is None:
            merged_graph.nodes.append(deepcopy(incoming_node))
            nodes_by_id[incoming_node.proposed_node_id] = merged_graph.nodes[-1]
            continue

        assert existing_node.node_type == incoming_node.node_type

        _append_unique(existing_node.names, incoming_node.names)
        if incoming_node.primary_reference_name:
            _append_unique(
                existing_node.names,
                [incoming_node.primary_reference_name],
            )
            existing_node.primary_reference_name = (
                incoming_node.primary_reference_name
            )
        _append_unique(existing_node.external_ids, incoming_node.external_ids)
        if incoming_node.summary:
            existing_node.summary = incoming_node.summary
        if incoming_node.type_specific is not None:
            if existing_node.type_specific is None:
                existing_node.type_specific = deepcopy(incoming_node.type_specific)
            else:
                existing_node.type_specific.update(
                    deepcopy(incoming_node.type_specific)
                )

    for incoming_edge in chunk_subgraph.edges:
        existing_edge = edges_by_id.get(incoming_edge.proposed_edge_id)
        if existing_edge is None:
            merged_graph.edges.append(deepcopy(incoming_edge))
            merged_graph.edges[-1].details = combine_edge_details(
                None, incoming_edge.details
            )
            edges_by_id[incoming_edge.proposed_edge_id] = merged_graph.edges[-1]
            continue

        assert (
            existing_edge.from_proposed_node_id
            == incoming_edge.from_proposed_node_id
            and existing_edge.to_proposed_node_id
            == incoming_edge.to_proposed_node_id
            and existing_edge.relation_type == incoming_edge.relation_type
        )

        _append_unique(existing_edge.evidence, incoming_edge.evidence)
        existing_edge.details = combine_edge_details(
            existing_edge.details, incoming_edge.details
        )

    return merged_graph


def _append_unique(existing: list, incoming: list) -> None:
    for item in incoming:
        if item not in existing:
            existing.append(deepcopy(item))


def _halt_on_proposed_subgraph_limit(
    graph: ProposedGraph,
    limits: ProposedSubgraphLimits,
) -> None:
    node_count = len(graph.nodes)
    edge_count = len(graph.edges)
    if node_count >= limits.max_nodes or edge_count >= limits.max_edges:
        raise ProposedSubgraphLimitError(
            "Proposed subgraph limit reached; continuation is not implemented, so "
            f"the ingest job was halted (nodes={node_count}/{limits.max_nodes}, "
            f"edges={edge_count}/{limits.max_edges})."
        )
