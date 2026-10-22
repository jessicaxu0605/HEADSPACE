export type MemoryNodeType =
  | "person"
  | "organization"
  | "location"
  | "event"
  | "document"
  | "date"
  | "task"
  | "concept";

export type MemoryNodeReference = {
  node_id: string;
  node_type: MemoryNodeType;
  title: string;
};

// A type-specific field the backend marked as worth showing (a task's due
// date, a person's pronouns). See TYPE_SPECIFIC_FIELDS in type_specific.py.
export type MemoryNodeDetail = {
  name: string;
  label: string;
  kind: "text" | "date" | "datetime" | "number" | "enum" | string;
  value: unknown;
};

export type MemoryExternalId = { provider: string; external_id: string };

export type MemoryNeighborhoodNode = MemoryNodeReference & {
  summary: string | null;
  importance: number;
  confidence: number;
  distance: number;
  // Other names for the node, excluding its title and external IDs.
  aliases: string[];
  external_ids: MemoryExternalId[];
  details: MemoryNodeDetail[];
};

export type MemoryGraphEdge = {
  edge_id: string;
  from_node_id: string;
  to_node_id: string;
  relation_type: string;
  connector: string;
  strength: number;
  confidence: number;
  details: string | null;
};

export type MemoryGraphNeighborhood = {
  start_node_id: string;
  depth: number;
  nodes: MemoryNeighborhoodNode[];
  edges: MemoryGraphEdge[];
};

export type MemoryEdgeStatus = "active" | "scheduled" | "expired" | "deleted";

export type MemoryRunChangeKind = "created" | "updated" | "archived" | "merged";

export type MemoryRunGraph = {
  run_id: string;
  source_id: string;
  run_status: "pending" | "processing" | "completed" | "failed";
  nodes: (MemoryNodeReference & {
    summary: string | null;
    importance: number;
    confidence: number;
    status: "active" | "merged" | "deleted";
    merged_into_node_id: string | null;
    change_kind: MemoryRunChangeKind;
  })[];
  edges: (MemoryGraphEdge & {
    status: MemoryEdgeStatus;
    deleted_reason: string | null;
    change_kind: MemoryRunChangeKind;
  })[];
};

export type MemoryEdgeEndpoint = MemoryNodeReference & {
  summary: string | null;
};

export type MemoryEdgeEvidence = {
  evidence_id: string;
  raw_content: string;
  confidence: number;
  strength_boost: number;
  is_verbatim: boolean;
  created_at: string;
  source: {
    source_id: string;
    source_type: string;
    source_metadata: Record<string, unknown>;
    // When the source was ingested.
    created_at: string;
  };
  annotations: { start: number; length: number; node_id: string }[];
};

export type MemoryEdgeDetail = {
  edge_id: string;
  relation_type: string;
  inverse_relation_type: string;
  relation_description: string | null;
  connector: string;
  is_symmetric: boolean;
  details: string | null;
  strength: number;
  confidence: number;
  status: MemoryEdgeStatus;
  valid_from: string | null;
  valid_to: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  deleted_reason: string | null;
  from_node: MemoryEdgeEndpoint;
  to_node: MemoryEdgeEndpoint;
  evidence: MemoryEdgeEvidence[];
};

// Landing page: GET /api/v1/landing. Each memory is a small subgraph around
// one anchor node or edge; see app/graph/queries/memory_surfacing.py.
export type MemoryLandingAnchor = {
  kind: "node" | "edge";
  id: string;
  // Source that chose the anchor, such as "open_tasks" or "on_this_day".
  reason: string;
  score: number;
  // Short display hint, such as "due tomorrow" or "3 years ago today".
  note: string | null;
  // Local ISO date the anchor is about, such as a due date or event day.
  when: string | null;
};

export type MemoryLandingNode = MemoryNodeReference & {
  summary: string | null;
  importance: number;
  details: MemoryNodeDetail[];
};

export type MemoryLandingEdge = MemoryGraphEdge & {
  status: MemoryEdgeStatus;
  valid_from: string | null;
  valid_to: string | null;
  // anchor: the surfaced edge; context: around the anchor; related:
  // semantically close edge from elsewhere in the graph.
  role: "anchor" | "context" | "related";
  similarity: number | null;
  // "{from} {connector} {to} (details)".
  sentence: string;
  evidence: {
    evidence_id: string;
    raw_content: string;
    is_verbatim: boolean;
    source_type: string;
    source_created_at: string;
    annotations: { start: number; length: number; node_id: string }[];
  } | null;
};

export type MemoryLandingCard = {
  anchor: MemoryLandingAnchor;
  pinned: boolean;
  // Anchor node (or the anchor edge's endpoints) first.
  nodes: MemoryLandingNode[];
  // Anchor edge first, then context, then related edges.
  edges: MemoryLandingEdge[];
};

export type MemoryLanding = {
  generated_at: string;
  timezone: string;
  // Unfinished tasks and upcoming events in date order, each with its
  // adjacent edges and nodes.
  upcoming: MemoryLandingCard[];
  // A varied draw of memories.
  memories: MemoryLandingCard[];
};
