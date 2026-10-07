"""Datatypes used by memory ingest workflows.

These are workflow/domain DTOs rather than database models. Persisted rows belong
in app.models; these shapes describe source preparation, LLM extraction payloads,
proposed graph state, and ingest handoff state.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID


JsonObject: TypeAlias = dict[str, Any]

SourceType: TypeAlias = Literal[
    "email",
    "text_message",
    "document",
    "pdf",
    "photo",
    "calendar_event",
    "manual_entry",
    "structured_import",
]

SourceReadStrategy: TypeAlias = Literal[
    "load_all",
    "stream",
    "pagewise",
    "recordwise",
]

NodeType: TypeAlias = Literal[
    "person",
    "location",
    "date",
    "task",
    "concept",
]

PatchActionType: TypeAlias = Literal[
    "add_node",
    "remove_node",
    "merge_nodes",
    "modify_node",
    "split_node",
    "add_edge",
    "remove_edge",
    "modify_edge",
    "add_edge_evidence",
]

MAX_LOAD_ALL_BYTES = 2 * 1024 * 1024

SOURCE_STRATEGIES: dict[str, SourceReadStrategy] = {
    "manual_entry": "load_all",
    "email": "load_all",
    "calendar_event": "load_all",
    "pdf": "pagewise",
    "plain_text_file": "stream",
    "csv_import": "recordwise",
    "email_archive": "recordwise",
}


@dataclass(frozen=True, slots=True)
class MemorySourceInput:
    workspace_id: UUID
    source_type: SourceType
    storage_object_key: str | None
    source_metadata: JsonObject
    source_idempotency_key: str


@dataclass(frozen=True, slots=True)
class MemorySourceRecord:
    workspace_id: UUID
    id: UUID
    source_type: str
    storage_object_key: str | None
    source_metadata: JsonObject
    source_idempotency_key: str


@dataclass(frozen=True, slots=True)
class ProposedExternalId:
    provider: str
    external_id: str


@dataclass(slots=True)
class ProposedNode:
    proposed_node_id: str
    node_type: NodeType
    names: list[str]
    primary_reference_name: str
    summary: str | None = None
    type_specific: JsonObject | None = None
    external_ids: list[ProposedExternalId] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProposedEvidenceAnnotation:
    start: int
    length: int
    local_node_id: str


@dataclass(frozen=True, slots=True)
class ProposedEvidence:
    raw_content: str
    annotations: list[ProposedEvidenceAnnotation] = field(default_factory=list)


@dataclass(slots=True)
class ProposedEdge:
    proposed_edge_id: str
    from_proposed_node_id: str
    to_proposed_node_id: str
    relation_type: str
    evidence: list[ProposedEvidence] = field(default_factory=list)
    confidence: float = 0.0
    strength: float | None = None


@dataclass(slots=True)
class ProposedGraph:
    nodes: list[ProposedNode] = field(default_factory=list)
    edges: list[ProposedEdge] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExtractionSourceContext:
    source_id: UUID
    source_type: SourceType | str
    extraction_metadata: JsonObject


@dataclass(frozen=True, slots=True)
class ChunkLocation:
    section_title: str | None = None
    page_number: int | None = None
    message_index: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True, slots=True)
class SourceChunk:
    chunk_id: str
    source_id: UUID
    text: str
    chunk_location: ChunkLocation = field(default_factory=ChunkLocation)


@dataclass(frozen=True, slots=True)
class PreparedSource:
    source_context: ExtractionSourceContext
    chunks: AsyncIterable[SourceChunk]


@dataclass(slots=True)
class ExtractionContext:
    source_summary: str
    current_topic: str | None = None
    speaker_context: str | None = None
    temporal_anchor: str | None = None
    unresolved_references: list[str] = field(default_factory=list)
    extraction_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProposedNodeHandoff:
    proposed_node_id: str
    node_type: NodeType | str
    names: list[str]
    primary_name: str
    handoff_aliases: list[str] = field(default_factory=list)
    external_ids: list[ProposedExternalId] = field(default_factory=list)
    handoff_description: str | None = None


@dataclass(frozen=True, slots=True)
class ProposedEdgeHandoff:
    proposed_edge_id: str
    from_proposed_node_id: str
    to_proposed_node_id: str
    relation_type: str


@dataclass(frozen=True, slots=True)
class ProposedGraphHandoff:
    nodes: list[ProposedNodeHandoff] = field(default_factory=list)
    edges: list[ProposedEdgeHandoff] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BoundaryContext:
    previous_chunk_tail: str | None = None
    next_chunk_head: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalNodeHandoff:
    canonical_node_id: UUID
    title: str
    node_type: NodeType | str
    identifiers: list[str] = field(default_factory=list)
    handoff_aliases: list[str] = field(default_factory=list)
    handoff: str | None = None
    external_ids: list[ProposedExternalId] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PreviousIngestContext:
    resolved_nodes: list[CanonicalNodeHandoff] = field(default_factory=list)
    handoff: str = ""


@dataclass(frozen=True, slots=True)
class ChunkExtractionInput:
    current_chunk: SourceChunk
    source_context: ExtractionSourceContext
    extraction_context: ExtractionContext
    proposed_graph_handoff: ProposedGraphHandoff
    boundary_context: BoundaryContext | None = None
    previous_ingest_context: PreviousIngestContext | None = None


@dataclass(frozen=True, slots=True)
class ExtractionRepairItem:
    message: str
    item_type: str | None = None
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChunkExtractionSnapshotOutput:
    proposed_graph: ProposedGraph
    proposed_graph_handoff: ProposedGraphHandoff
    updated_extraction_context: ExtractionContext
    repair_items: list[ExtractionRepairItem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AddNodeAction:
    node: ProposedNode
    type: Literal["add_node"] = "add_node"


@dataclass(frozen=True, slots=True)
class RemoveNodeAction:
    proposed_node_id: str
    type: Literal["remove_node"] = "remove_node"


@dataclass(frozen=True, slots=True)
class ModifyNodeAction:
    proposed_node_id: str
    add_names: list[str] = field(default_factory=list)
    remove_names: list[str] = field(default_factory=list)
    set_primary_name: str | None = None
    merge_type_specific: JsonObject | None = None
    add_external_ids: list[ProposedExternalId] = field(default_factory=list)
    type: Literal["modify_node"] = "modify_node"


@dataclass(frozen=True, slots=True)
class MergeNodesAction:
    from_proposed_node_id: str
    into_proposed_node_id: str
    add_names: list[str] = field(default_factory=list)
    set_primary_name: str | None = None
    merge_type_specific: JsonObject | None = None
    add_external_ids: list[ProposedExternalId] = field(default_factory=list)
    type: Literal["merge_nodes"] = "merge_nodes"


@dataclass(frozen=True, slots=True)
class AddEdgeAction:
    edge: ProposedEdge
    type: Literal["add_edge"] = "add_edge"


@dataclass(frozen=True, slots=True)
class RemoveEdgeAction:
    proposed_edge_id: str
    type: Literal["remove_edge"] = "remove_edge"


@dataclass(frozen=True, slots=True)
class ModifyEdgeAction:
    proposed_edge_id: str
    set_relation_type: str | None = None
    set_confidence: float | None = None
    set_strength: float | None = None
    type: Literal["modify_edge"] = "modify_edge"


@dataclass(frozen=True, slots=True)
class AddEdgeEvidenceAction:
    proposed_edge_id: str
    evidence: list[ProposedEvidence]
    type: Literal["add_edge_evidence"] = "add_edge_evidence"


@dataclass(frozen=True, slots=True)
class MoveEvidenceAnnotation:
    proposed_edge_id: str
    evidence_index: int
    annotation_index: int


@dataclass(frozen=True, slots=True)
class SplitNodeAction:
    from_proposed_node_id: str
    create_node: ProposedNode
    move_names: list[str] = field(default_factory=list)
    move_external_ids: list[ProposedExternalId] = field(default_factory=list)
    move_edge_ids: list[str] = field(default_factory=list)
    move_evidence_annotations: list[MoveEvidenceAnnotation] = field(default_factory=list)
    type: Literal["split_node"] = "split_node"


PatchAction: TypeAlias = (
    AddNodeAction
    | RemoveNodeAction
    | MergeNodesAction
    | ModifyNodeAction
    | SplitNodeAction
    | AddEdgeAction
    | RemoveEdgeAction
    | ModifyEdgeAction
    | AddEdgeEvidenceAction
)


@dataclass(frozen=True, slots=True)
class ChunkExtractionPatchOutput:
    proposed_graph_patch: list[PatchAction]
    updated_extraction_context: ExtractionContext


class SourceAdapter(Protocol):
    preferred_strategy: SourceReadStrategy

    def build_extraction_metadata(
        self,
        source_metadata: JsonObject,
    ) -> JsonObject:
        ...

    def create_chunk_stream(
        self,
        payload: Any,
        source: Any,
    ) -> AsyncIterable[SourceChunk]:
        ...
