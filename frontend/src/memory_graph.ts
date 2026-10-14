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

export type MemoryNeighborhoodNode = MemoryNodeReference & {
  summary: string | null;
  importance: number;
  confidence: number;
  distance: number;
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
